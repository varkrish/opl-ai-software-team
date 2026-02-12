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
    "and always writing complete file content via the file_writer tool."
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
    ):
        self.workspace_path = Path(workspace_path)
        self.project_id = project_id
        tools = create_workspace_file_tools(self.workspace_path)
        tracker = budget_tracker or EnhancedBudgetTracker()
        tracker.project_id = project_id

        backstory = load_prompt(
            "migration/apply_changes.txt", fallback=EXECUTION_BACKSTORY
        )

        self.agent = BaseLlamaIndexAgent(
            role="Migration Execution Agent",
            goal="Apply the specified migration changes to the target file precisely.",
            backstory=backstory,
            tools=tools,
            agent_type="worker",
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
        sections.append(f"## Current content of {file_path}\n```\n{file_content}\n```")

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

        sections.append(
            f"## Instructions\n"
            f"Apply all the migration issues listed above to **{file_path}**.\n"
            f"Call `file_writer` with file_path=\"{file_path}\" and the COMPLETE updated content.\n"
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
    ) -> str:
        prompt = self.build_prompt(
            file_path=file_path,
            file_content=file_content,
            issues=issues,
            migration_goal=migration_goal,
            repo_rules=repo_rules,
            user_notes=user_notes,
            uploaded_docs_context=uploaded_docs_context,
        )
        return str(self.agent.chat(prompt))
