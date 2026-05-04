"""
Refinement Agent - Applies user natural-language edit instructions to the codebase.
Uses workspace-bound file tools only (no git/pytest). Context-rich prompt for quality.
"""
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any

from .base_agent import BaseLlamaIndexAgent
from ..tools.file_tools import create_workspace_file_tools
from ..budget.tracker import EnhancedBudgetTracker

logger = logging.getLogger(__name__)

REFINEMENT_SYSTEM_FALLBACK = """You are a Refinement Agent. Your goal is to apply the user's natural-language edit instruction to the codebase.

You MUST use the provided tools to accomplish the task. Do NOT just describe what you would do - actually DO it using the tools.

Available tools:
- file_lister: Recursively list all files in a directory to discover project structure.
- file_reader: Read the current content of a file before modifying it.
- file_writer: Write the modified content back to a file. You MUST call this for EVERY file you want to change (create or update).
- file_deleter: Delete a file from the workspace. Use this when the user asks to remove, delete, or get rid of a file. The file will be removed from the filesystem.

CRITICAL RULES:
- You MUST call file_writer for every file you modify (create or update). Without file_writer, NO changes are saved.
- When the user asks to DELETE, REMOVE, or get rid of a file: call file_deleter with that file_path. Do NOT empty the file with file_writer — that leaves an empty file; file_deleter actually removes the file.
- When writing files, write the COMPLETE file content, not just a snippet or diff.
- Make minimal, targeted changes that satisfy the user's request.
- Do not invent new features or refactor beyond what was asked.
- Respect the technology stack and patterns already in the project.
- NEVER say "I have made the changes" without actually calling file_writer or file_deleter first.
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
    ) -> str:
        """Build a focused prompt for the agent.

        The runner always provides a single file_path (even for project-wide scope,
        it iterates and calls once per file). This keeps the LLM context small and fast.
        """
        import re as _re
        sections: List[str] = []
        sections.append("## User request\n" + user_prompt)

        # Detect EXPLICIT whole-file delete intent only — phrases like "delete this file",
        # "remove this file". Words like "remove", "delete" alone (e.g. "remove ff_* tools")
        # are NOT file-delete requests.
        prompt_lower = user_prompt.strip().lower()
        _explicit_file_delete_phrases = (
            "delete this file", "delete the file", "remove this file", "remove the file",
            "delete file", "remove file", "get rid of this file", "erase this file",
        )
        is_delete_request = file_path and any(p in prompt_lower for p in _explicit_file_delete_phrases)

        # ── Target file ────────────────────────────────────────────────
        if file_path:
            sections.append(f"\n## Target file\nYou must ONLY modify: **{file_path}**")
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

        if tech_stack_content:
            # Truncate tech stack to avoid bloating single-file prompts
            tc = tech_stack_content[:1500] + "..." if len(tech_stack_content) > 1500 else tech_stack_content
            sections.append("\n## Project tech stack (respect this)\n" + tc)

        # file_listing is only useful in project-wide (no file_path) mode
        if file_listing and not file_path:
            sections.append("\n## Other files in the workspace (for reference only)\n" + file_listing)

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
            if large_file:
                sections.append(f"""
## Instructions
The file **{file_path}** is large. Only the relevant sections are shown above.
Follow these steps exactly:
1. Call file_reader with file_path="{file_path}" to load the COMPLETE current content.
2. If the user explicitly asks to DELETE THE ENTIRE FILE (e.g. "delete this file", "remove this file entirely"): call file_deleter with file_path="{file_path}". Then provide a Final Answer.
3. Otherwise — apply the user's changes to the COMPLETE content you read and call file_writer with file_path="{file_path}" and the FULL updated file (not a diff, not just the changed parts).
4. Provide a Final Answer summarizing what you changed.

CRITICAL RULES:
- ONLY modify **{file_path}**. Do NOT touch other files.
- "Remove lines / delete code / remove functions" means EDIT the file — do NOT delete it.
- You MUST call file_writer with the COMPLETE file — without it, nothing is saved.""")
            else:
                sections.append(f"""
## Instructions
Follow these steps exactly:
1. The content of **{file_path}** is provided above. Read it carefully.
2. If the user explicitly asks to DELETE THE ENTIRE FILE (e.g. "delete this file", "remove this file entirely"): call file_deleter with file_path="{file_path}". Then provide a Final Answer.
3. Otherwise — even if the request says "remove lines", "delete code", "remove functions" — apply the changes to the file content and call file_writer with file_path="{file_path}" and the COMPLETE updated file content (entire file, not a diff). NEVER call file_deleter just because the user wants to remove lines or code within the file.
4. Provide a Final Answer summarizing what you changed.

CRITICAL RULES:
- ONLY modify **{file_path}**. Do NOT touch other files.
- "Remove lines / delete code / remove functions" means EDIT the file with file_writer — NOT delete the file with file_deleter.
- file_deleter is ONLY for when the user explicitly wants the entire file gone.
- You MUST call file_writer — without it, nothing is saved.
- Write the COMPLETE file content when using file_writer (not just the changed parts).
- If the user's request does not apply to this file, call file_writer anyway with the original content unchanged and explain in your Final Answer.""")
        else:
            sections.append("""
## Instructions
1. Call file_lister(".") to discover all files in the project.
2. Use file_reader to read each relevant source file.
3. Requests to "remove lines", "delete code", "remove functions/tools" mean EDIT the file with file_writer — keep the file, just remove the specified content.
4. Only use file_deleter if the user explicitly asks to DELETE AN ENTIRE FILE (e.g. "delete this file", "remove this file entirely").
5. For each file to modify: call file_writer with the COMPLETE updated content (entire file).
6. Provide a Final Answer listing all files you modified.

CRITICAL: "Remove code/lines/functions" = file_writer with edited content. file_deleter = only for deleting entire files on explicit request. Without calling file_writer, nothing is saved.""")

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
            )
        else:
            prompt = self.build_prompt(
                user_prompt=user_prompt,
                file_path=file_path,
                tech_stack_content=tech_stack_content,
                file_listing=file_listing,
                refinement_history=refinement_history,
                initial_file_content=initial_file_content,
            )
        return str(self.agent.chat(prompt))

    def _build_batched_prompt(
        self,
        user_prompt: str,
        candidate_files: Dict[str, str],
        tech_stack_content: Optional[str] = None,
        refinement_history: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Build a single prompt that includes ALL candidate files for one-shot editing."""
        sections: List[str] = ["## User request\n" + user_prompt]

        if tech_stack_content:
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
