"""
Refinement Agent - Applies user natural-language edit instructions to the codebase.
Uses workspace-bound file tools only (no git/pytest). Context-rich prompt for quality.
"""
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any

from .base_agent import BaseLlamaIndexAgent
from ..tools.file_tools import create_workspace_file_tools
from ..tools.tldr_tools import append_tldr_tools
from ..budget.tracker import EnhancedBudgetTracker

logger = logging.getLogger(__name__)

REFINEMENT_SYSTEM_FALLBACK = """You are a Refinement Agent. Your goal is to apply the user's natural-language edit instruction to the codebase.

You MUST use the provided tools to accomplish the task. Do NOT just describe what you would do - actually DO it using the tools.

Available tools:
- file_lister: Recursively list all files in a directory to discover project structure.
- file_reader: Read the current content of a file before modifying it.
- replace_file_content: Replace a specific range of lines in an existing file. You MUST use this to update files. NEVER overwrite the entire file.
- file_writer: Write NEW files from scratch. Do NOT use this for updating existing files.
- file_deleter: Delete a file from the workspace. Use this when the user asks to remove, delete, or get rid of a file.
- code_search: Search the codebase for a regex pattern. Returns matching lines with context.
  Use BEFORE editing to find all usages of a function, class, or variable you plan to change.
- code_structure: Show classes, functions, and exports across the entire project.
- code_context: Get the call-chain context for a function or method.
- code_impact: Find all callers of a function (reverse call graph).

CRITICAL RULES:
- You MUST call replace_file_content for every existing file you want to update.
- Do NOT use file_writer for updating files! It will delete the rest of the file. Use replace_file_content with start_line and end_line.
- When the user asks to DELETE, REMOVE, or get rid of a file: call file_deleter with that file_path.
- Make minimal, targeted changes that satisfy the user's request.
- Do not invent new features or refactor beyond what was asked.
- BEFORE modifying or deleting any function or class, call code_search or code_impact to find all usages. Update every call site.
- NEVER say "I have made the changes" without actually calling replace_file_content, file_writer, or file_deleter first.
"""


class RefinementAgent:
    """Agent that applies user edit prompts to the workspace using file tools only."""

    def __init__(
        self,
        workspace_path: Path,
        project_id: str,
        budget_tracker: Optional[EnhancedBudgetTracker] = None,
    ):
        """
        Initialize RefinementAgent with workspace-bound tools (thread-safe).

        Args:
            workspace_path: Job workspace root. Tools will operate only under this path.
            project_id: Job ID for budget tracking.
            budget_tracker: Optional; if not provided, one is created with project_id set.
        """
        self.workspace_path = Path(workspace_path)
        self.project_id = project_id
        tools = create_workspace_file_tools(self.workspace_path)
        append_tldr_tools(tools, self.workspace_path)
        logger.debug("RefinementAgent: tldr tools wired for workspace %s", self.workspace_path)
        tracker = budget_tracker or EnhancedBudgetTracker()
        tracker.project_id = project_id
        self.agent = BaseLlamaIndexAgent(
            role="Refinement Agent",
            goal="Apply the user's edit instruction to the codebase with minimal, precise changes.",
            backstory=REFINEMENT_SYSTEM_FALLBACK,
            tools=tools,
            agent_type="worker",
            budget_tracker=tracker,
            verbose=True,
        )

    # Maximum characters of a single file's content to inline in the prompt.
    # Files exceeding this limit are context-extracted (header + relevant sections only).
    _MAX_INLINE_CHARS = 4000

    @staticmethod
    def _extract_relevant_content(content: str, tokens: List[str], header_lines: int = 30,
                                   context_window: int = 15) -> str:
        """For large files, return header lines + sections relevant to `tokens`.

        Lines containing any of `tokens` are included together with `context_window`
        lines before and after. A comment marker is inserted where lines are omitted.
        """
        lines = content.splitlines()
        if not lines:
            return content

        # Always keep the file header (imports / package declaration)
        always_keep = set(range(min(header_lines, len(lines))))

        # Find lines that contain any of the tokens
        relevant: set = set(always_keep)
        for i, line in enumerate(lines):
            if any(tok in line for tok in tokens):
                for j in range(max(0, i - context_window), min(len(lines), i + context_window + 1)):
                    relevant.add(j)

        sorted_idx = sorted(relevant)
        result: List[str] = []
        prev = -2
        for ln in sorted_idx:
            gap = ln - prev - 1
            if gap > 0:
                result.append(f"// ... ({gap} lines omitted) ...")
            result.append(lines[ln])
            prev = ln
        trailing = len(lines) - 1 - sorted_idx[-1] if sorted_idx else 0
        if trailing > 0:
            result.append(f"// ... ({trailing} lines omitted) ...")

        return "\n".join(result)

    def build_prompt(
        self,
        user_prompt: str,
        file_path: Optional[str] = None,
        tech_stack_content: Optional[str] = None,
        file_listing: Optional[str] = None,
        refinement_history: Optional[List[Dict[str, Any]]] = None,
        initial_file_content: Optional[str] = None,
        *,
        scope: str = "project",
        allowed_files: Optional[List[str]] = None,
        project_context: Optional[str] = None,
    ) -> str:
        """Build a focused prompt for the agent.

        scope: ``project`` | ``impact`` | ``file`` (primary file only).
        """
        import re as _re
        sections: List[str] = []
        sections.append("## User request\n" + user_prompt)

        if project_context:
            sections.append("\n## Project context\n" + project_context)

        # Detect EXPLICIT whole-file delete intent only — phrases like "delete this file",
        # "remove this file". Words like "remove", "delete" alone (e.g. "remove ff_* tools")
        # are NOT file-delete requests.
        prompt_lower = user_prompt.strip().lower()
        _explicit_file_delete_phrases = (
            "delete this file", "delete the file", "remove this file", "remove the file",
            "delete file", "remove file", "get rid of this file", "erase this file",
        )
        file_path and any(p in prompt_lower for p in _explicit_file_delete_phrases)

        # ── Target file ────────────────────────────────────────────────
        if file_path:
            if scope == "impact" and allowed_files and len(allowed_files) > 1:
                others = [f for f in allowed_files if f != file_path]
                sections.append(
                    f"\n## Primary target\n**{file_path}**"
                    f"\nYou may also edit these related files for call-site consistency: "
                    + ", ".join(f"**{f}**" for f in others)
                )
            elif scope == "file":
                sections.append(f"\n## Target file\nYou must ONLY modify: **{file_path}**")
            else:
                sections.append(f"\n## Primary focus\nStart with: **{file_path}**")
            if initial_file_content is not None:
                # For large files, extract only the relevant sections to fit context window.
                # The LLM sees a trimmed view; the instructions direct it to use file_reader
                # to fetch the full content before calling file_writer with the complete file.
                if len(initial_file_content) > self._MAX_INLINE_CHARS:
                    prompt_no_urls = _re.sub(r'https?://\S+', '', user_prompt)
                    tokens = list({
                        t.rstrip("*") for t in
                        _re.findall(r'([\w]+(?:_[\w]*)*)\*', prompt_no_urls) +
                        _re.findall(r'[`"\']([^`"\']{2,40})[`"\']', prompt_no_urls) +
                        _re.findall(r'\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b', prompt_no_urls)
                        if len(t) >= 2
                    }) or [file_path.split("/")[-1].split(".")[0]]
                    display_content = self._extract_relevant_content(initial_file_content, tokens)
                    total_lines = len(initial_file_content.splitlines())
                    shown_lines = len(display_content.splitlines())
                    sections.append(
                        f"\n## Relevant sections of {file_path} "
                        f"({shown_lines} of {total_lines} lines shown; rest unchanged)\n```\n"
                        + display_content
                        + "\n```"
                    )
                else:
                    sections.append(
                        f"\n## Current content of {file_path}\n```\n"
                        + initial_file_content
                        + "\n```"
                    )

        if tech_stack_content and not project_context:
            tc = tech_stack_content[:1500] + "..." if len(tech_stack_content) > 1500 else tech_stack_content
            sections.append("\n## Project tech stack (respect this)\n" + tc)

        if file_listing and (not file_path or scope == "project"):
            sections.append("\n## Files in the workspace (for reference)\n" + file_listing)

        if refinement_history:
            history_text = "\n".join(
                f"- [{r.get('status', '')}] {r.get('prompt', '')[:200]}..."
                for r in refinement_history[:5]
            )
            sections.append("\n## Previous refinement requests (do not undo these)\n" + history_text)

        # Determine whether full file content was inlined (affects instructions)
        large_file = (
            initial_file_content is not None
            and len(initial_file_content) > self._MAX_INLINE_CHARS
        )

        # ── Instructions ───────────────────────────────────────────────
        if file_path:
            allowed_note = ""
            if scope == "impact" and allowed_files and len(allowed_files) > 1:
                allowed_note = (
                    f"\n- You MAY modify: {', '.join(allowed_files)}. "
                    "Do NOT edit files outside this set unless necessary for imports."
                )
            elif scope == "file":
                allowed_note = f"\n- ONLY modify **{file_path}**. Do NOT touch other files."

            if large_file:
                sections.append(f"""
## Instructions
The file **{file_path}** is large. Only the relevant sections are shown above.
Follow these steps exactly:
0. Use code_impact / code_search BEFORE renaming or deleting functions to find call sites.
1. Call file_reader with file_path="{file_path}" to load the COMPLETE current content.
2. If the user explicitly asks to DELETE THE ENTIRE FILE: call file_deleter with file_path="{file_path}".
3. Otherwise apply changes using replace_file_content to patch specific line ranges.
4. Provide a Final Answer summarizing what you changed.

CRITICAL RULES:{allowed_note}
- You MUST use replace_file_content with start_line and end_line for existing files. DO NOT use file_writer!""")
            else:
                sections.append(f"""
## Instructions
0. Use code_impact / code_search BEFORE renaming or deleting functions to find call sites.
1. Read the content of **{file_path}** above (or via file_reader if needed).
2. Apply the user's changes using replace_file_content for specific line ranges.
3. Provide a Final Answer summarizing what you changed.

CRITICAL RULES:{allowed_note}
- You MUST use replace_file_content. DO NOT use file_writer to overwrite the file!""")
        else:
            sections.append("""
## Instructions
0. (Optional) Use code_structure to get a project map before diving into files.
   Use code_search or code_impact BEFORE renaming/deleting functions to find all call sites.
1. Call file_lister(".") to discover all files in the project.
2. Use file_reader to read each relevant source file.
3. Requests to "remove lines", "delete code" mean EDIT the file with replace_file_content using a blank replacement_content.
4. Only use file_deleter if the user explicitly asks to DELETE AN ENTIRE FILE (e.g. "delete this file").
5. For each file to modify: call replace_file_content with start_line and end_line.
6. Provide a Final Answer listing all files you modified.

CRITICAL: "Remove code/lines/functions" = replace_file_content with empty replacement. file_deleter = only for deleting entire files. DO NOT use file_writer for existing files!""")

        return "\n".join(sections)

    def run(
        self,
        user_prompt: str,
        file_path: Optional[str] = None,
        tech_stack_content: Optional[str] = None,
        file_listing: Optional[str] = None,
        refinement_history: Optional[List[Dict[str, Any]]] = None,
        initial_file_content: Optional[str] = None,
        candidate_files: Optional[Dict[str, str]] = None,
        scope: str = "project",
        allowed_files: Optional[List[str]] = None,
        project_context: Optional[str] = None,
    ) -> str:
        """
        Run refinement: build context-rich prompt and execute agent.

        Args:
            user_prompt: Natural-language edit instruction.
            file_path: Optional path to focus on (relative to workspace).
            tech_stack_content: Content of tech_stack.md if present.
            file_listing: Output of file_lister(".") if whole-project.
            refinement_history: Last N refinements for context.
            initial_file_content: Pre-loaded content of target file if file_path set.

        Returns:
            Agent response string.
        """
        # Batched mode: multiple files provided at once — single LLM call
        if candidate_files:
            prompt = self._build_batched_prompt(
                user_prompt=user_prompt,
                candidate_files=candidate_files,
                tech_stack_content=tech_stack_content,
                refinement_history=refinement_history,
                project_context=project_context,
            )
        else:
            prompt = self.build_prompt(
                user_prompt=user_prompt,
                file_path=file_path,
                tech_stack_content=tech_stack_content,
                file_listing=file_listing,
                refinement_history=refinement_history,
                initial_file_content=initial_file_content,
                scope=scope,
                allowed_files=allowed_files,
                project_context=project_context,
            )
        return str(self.agent.chat(prompt))

    def _build_batched_prompt(
        self,
        user_prompt: str,
        candidate_files: Dict[str, str],
        tech_stack_content: Optional[str] = None,
        refinement_history: Optional[List[Dict[str, Any]]] = None,
        project_context: Optional[str] = None,
    ) -> str:
        """Build a single prompt that includes ALL candidate files for one-shot editing."""
        sections: List[str] = ["## User request\n" + user_prompt]

        if project_context:
            sections.append("\n## Project context\n" + project_context)
        elif tech_stack_content:
            sections.append("\n## Project tech stack\n" + tech_stack_content)

        if refinement_history:
            history_text = "\n".join(
                f"- [{r.get('status', '')}] {r.get('prompt', '')[:200]}..."
                for r in refinement_history[:3]
            )
            sections.append("\n## Previous refinements (do not undo)\n" + history_text)

        sections.append(f"\n## Files to edit ({len(candidate_files)} files)\n")
        for fp, content in candidate_files.items():
            truncated = content[:6000] + "\n... (truncated)" if len(content) > 6000 else content
            sections.append(f"### {fp}\n```\n{truncated}\n```\n")

        sections.append("""
## Instructions
Apply the user's request to ALL the files listed above in a single pass.
For each file that needs changes:
1. Call file_writer with file_path="<path>" and the COMPLETE updated content.
2. "Remove lines / delete code / remove functions" means EDIT with file_writer — keep the file.
3. Only use file_deleter if the user explicitly asks to delete an entire file by name.
4. If a file needs no changes, skip it (do NOT call file_writer for unchanged files).
5. After updating all files, provide a Final Answer listing every file you modified.

CRITICAL: Call file_writer for EACH file that needs changes. Without it, nothing is saved.""")

        return "\n".join(sections)
