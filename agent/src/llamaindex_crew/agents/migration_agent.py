"""
Migration Agents — MTA report analysis and code change execution.

Two agents:
  MigrationAnalysisAgent  — reads an MTA report (any format) and writes migration_plan.json
  MigrationExecutionAgent — applies migration changes to a single file using 4-tier context
"""
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any

from .base_agent import BaseLlamaIndexAgent
from ..tools.file_tools import create_workspace_file_tools
from ..budget.tracker import EnhancedBudgetTracker
from ..utils.prompt_loader import load_prompt
from ..utils.llm_config import get_llm_for_agent

# Migration execution: balance output length vs MaaS limits. 8192 helps avoid truncation;
# runner skips (does not fail) if still truncated after 6 attempts.
_MIGRATION_MIN_MAX_TOKENS = 4096
_MIGRATION_MAX_TOKENS_CAP = 8192

logger = logging.getLogger(__name__)

# ── Fallback backstories (used if prompt files are missing) ─────────────────

ANALYSIS_BACKSTORY = (
    "You are an expert at reading MTA (Migration Toolkit for Applications) reports "
    "in any format — JSON, CSV, HTML, YAML, or plain text — and extracting "
    "structured, actionable migration plans from them."
)

EXECUTION_BACKSTORY = (
    "You are a senior software engineer specializing in legacy code modernization. "
    "You apply migration changes precisely, respecting existing code patterns, "
    "and always writing the COMPLETE file via the file_writer tool.\n\n"
    "CRITICAL RULES:\n"
    "- You MUST output the ENTIRE file in one file_writer call. The written file "
    "must be roughly the same size as the original (same line count).\n"
    "- Do NOT truncate, summarize, abbreviate, or omit any part of the file.\n"
    "- Every original line must appear in your output, modified ONLY where required.\n"
    "- Do NOT add methods, fields, or imports that are not in the original file.\n"
    "- Do NOT fabricate or hallucinate code that was not present.\n"
    "- Preserve ALL comments, formatting, and structure from the original.\n"
    "- If unsure about a change, leave the original code unchanged."
)


# ═══════════════════════════════════════════════════════════════════════════════
# MigrationAnalysisAgent
# ═══════════════════════════════════════════════════════════════════════════════

class MigrationAnalysisAgent:
    """Reads an MTA report and produces a structured migration_plan.json."""

    def __init__(
        self,
        workspace_path: Path,
        project_id: str,
        budget_tracker: Optional[EnhancedBudgetTracker] = None,
    ):
        self.workspace_path = Path(workspace_path)
        self.project_id = project_id
        tools = create_workspace_file_tools(self.workspace_path)
        tracker = budget_tracker or EnhancedBudgetTracker()
        tracker.project_id = project_id

        backstory = load_prompt(
            "migration/analyze_report.txt", fallback=ANALYSIS_BACKSTORY
        )

        self.agent = BaseLlamaIndexAgent(
            role="Migration Analysis Agent",
            goal="Read the MTA report and produce a structured migration_plan.json with every actionable issue.",
            backstory=backstory,
            tools=tools,
            agent_type="worker",
            budget_tracker=tracker,
            verbose=True,
        )

    def build_prompt(
        self,
        report_path: str,
        migration_goal: str,
        file_listing: Optional[str] = None,
        user_notes: Optional[str] = None,
    ) -> str:
        """Build the analysis prompt from inputs."""
        sections: List[str] = []

        sections.append(f"## Migration goal\n{migration_goal}")
        sections.append(f"## MTA report location\nRead the report at: **{report_path}**")

        if file_listing:
            sections.append(f"## Codebase files\n{file_listing}")

        if user_notes:
            sections.append(f"## Additional instructions from the user\n{user_notes}")

        sections.append(
            "## Output\n"
            "Write the migration plan to **migration_plan.json** using `file_writer`. "
            "Follow the schema described in your instructions."
        )

        return "\n\n".join(sections)

    def run(
        self,
        report_path: str,
        migration_goal: str,
        file_listing: Optional[str] = None,
        user_notes: Optional[str] = None,
    ) -> str:
        prompt = self.build_prompt(
            report_path=report_path,
            migration_goal=migration_goal,
            file_listing=file_listing,
            user_notes=user_notes,
        )
        return str(self.agent.chat(prompt))


# ═══════════════════════════════════════════════════════════════════════════════
# MigrationExecutionAgent
# ═══════════════════════════════════════════════════════════════════════════════

class MigrationExecutionAgent:
    """Applies migration changes to a single file using 4-tier context injection."""

    def __init__(
        self,
        workspace_path: Path,
        project_id: str,
        budget_tracker: Optional[EnhancedBudgetTracker] = None,
        attempt: int = 1,
    ):
        self.workspace_path = Path(workspace_path)
        self.project_id = project_id
        tools = create_workspace_file_tools(self.workspace_path)
        tracker = budget_tracker or EnhancedBudgetTracker()
        tracker.project_id = project_id

        backstory = load_prompt(
            "migration/apply_changes.txt", fallback=EXECUTION_BACKSTORY
        )

        llm = get_llm_for_agent("worker")
        # Attempt 1: temperature=0 for deterministic output.
        # Retries: escalate temperature so the model produces genuinely different output
        # instead of repeating the same truncated result.
        temp = 0.0 if attempt <= 1 else min(0.1 * attempt, 0.7)
        if hasattr(llm, "temperature"):
            llm.temperature = temp
        if hasattr(llm, "max_tokens"):
            requested = max(llm.max_tokens, _MIGRATION_MIN_MAX_TOKENS)
            llm.max_tokens = min(requested, _MIGRATION_MAX_TOKENS_CAP)
        logger.info("Migration execution: attempt=%d, temperature=%.1f, max_tokens=%s", attempt, temp, getattr(llm, "max_tokens", "?"))

        self.agent = BaseLlamaIndexAgent(
            role="Migration Execution Agent",
            goal="Apply the specified migration changes to the target file precisely.",
            backstory=backstory,
            tools=tools,
            agent_type="worker",
            llm=llm,
            budget_tracker=tracker,
            verbose=True,
        )

    def build_prompt(
        self,
        file_path: str,
        file_content: str,
        issues: List[Dict[str, Any]],
        migration_goal: str,
        repo_rules: Optional[str] = None,
        user_notes: Optional[str] = None,
        uploaded_docs_context: Optional[str] = None,
        truncated: bool = False,
    ) -> str:
        """Build the execution prompt with 4-tier context injection.

        Tier 1 (System): Baked into backstory via apply_changes.txt
        Tier 2 (Repo):   repo_rules from .migration-rules.md
        Tier 3 (Uploaded): uploaded_docs_context from workspace/docs/
        Tier 4 (Per-run): user_notes free text
        """
        sections: List[str] = []

        sections.append(f"## Migration goal\n{migration_goal}")
        sections.append(f"## Target file\nYou must ONLY modify: **{file_path}**")

        # Add line numbers to content (crucial for surgical edits)
        lines = file_content.splitlines()
        numbered_content = "\n".join(f"{i+1:4}: {line}" for i, line in enumerate(lines))

        if truncated:
            sections.append(
                f"## Current content of {file_path} (TRUNCATED)\n"
                f"**WARNING**: The content below is truncated because the file is very large.\n"
                f"You MUST call `file_reader` with file_path=\"{file_path}\" to read the full "
                f"file content before making any changes.\n"
                f"```\n{numbered_content}\n```"
            )
        else:
            sections.append(f"## Current content of {file_path}\n```\n{numbered_content}\n```")

        # Build issues section
        issues_text = []
        for issue in issues:
            issues_text.append(
                f"### {issue.get('id', '?')}: {issue.get('title', 'Untitled')}\n"
                f"**Hint:** {issue.get('migration_hint', 'No hint provided')}"
            )
        sections.append("## Migration issues for this file\n" + "\n\n".join(issues_text))

        # Tier 2: Repository convention rules
        if repo_rules:
            sections.append(f"## Migration rules (from repository)\n{repo_rules}")

        # Tier 3: Uploaded migration guides
        if uploaded_docs_context:
            sections.append(f"## Reference documents\n{uploaded_docs_context}")

        # Tier 4: Per-run user notes
        if user_notes:
            sections.append(f"## Additional instructions from the user\n{user_notes}")

        if truncated:
            sections.append(
                f"## Instructions\n"
                f"Because this file is very large, do NOT use `file_writer` to rewrite the whole file.\n"
                f"Instead, use `file_line_replacer` to make surgical changes to specific line ranges.\n\n"
                f"1. Identify the line ranges that need to change.\n"
                f"2. Call `file_line_replacer` one or more times to apply the issues.\n"
                f"3. Make sure to preserve the surrounding structure and comments.\n\n"
                f"Provide a Final Answer listing what you changed."
            )
        else:
            sections.append(
                f"## Instructions\n"
                f"Apply all the migration issues listed above to **{file_path}**.\n"
                f"Call `file_writer` with file_path=\"{file_path}\" and the COMPLETE updated content.\n\n"
                f"CRITICAL — you MUST write the ENTIRE file in one file_writer call:\n"
                f"- The written file must be the same length as the original (or very close).\n"
                f"- Do NOT truncate, summarize, or omit any part of the file.\n"
                f"- Contain EVERY line of the original file (modified only where needed).\n"
                f"- Keep the same package, class name, all fields, all methods, all comments.\n"
                f"- NOT add any new methods, fields, or imports that were not in the original.\n"
                f"- If you cannot output the full file in one go, the migration will fail.\n\n"
                f"Provide a Final Answer listing what you changed."
            )

        return "\n\n".join(sections)

    def run(
        self,
        file_path: str,
        file_content: str,
        issues: List[Dict[str, Any]],
        migration_goal: str,
        repo_rules: Optional[str] = None,
        user_notes: Optional[str] = None,
        uploaded_docs_context: Optional[str] = None,
        truncated: bool = False,
    ) -> str:
        prompt = self.build_prompt(
            file_path=file_path,
            file_content=file_content,
            issues=issues,
            migration_goal=migration_goal,
            repo_rules=repo_rules,
            user_notes=user_notes,
            uploaded_docs_context=uploaded_docs_context,
            truncated=truncated,
        )
        return str(self.agent.chat(prompt))
