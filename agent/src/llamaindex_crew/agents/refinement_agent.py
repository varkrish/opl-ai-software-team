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
        sections: List[str] = []
        sections.append("## User request\n" + user_prompt)

        # Detect explicit delete intent so we can force file_deleter
        prompt_lower = user_prompt.strip().lower()
        delete_keywords = ("delete", "remove", "get rid of", "erase", "discard")
        user_wants_delete = any(k in prompt_lower for k in delete_keywords)
        # If target file name appears in request or request is clearly about deleting "this" file
        file_name_in_request = file_path and (
            (file_path in user_prompt) or (Path(file_path).name in user_prompt)
        )
        is_delete_request = user_wants_delete and (file_path and (file_name_in_request or "file" in prompt_lower or "this" in prompt_lower))

        # ── Target file ────────────────────────────────────────────────
        if file_path:
            sections.append(f"\n## Target file\nYou must ONLY modify or delete: **{file_path}**")
            if is_delete_request:
                sections.append(
                    f'\n*** ACTION REQUIRED: The user is asking to DELETE this file. '
                    f'You MUST call the file_deleter tool with file_path="{file_path}" (and no other tool). '
                    f'Do NOT use file_writer. Call file_deleter now, then give a short Final Answer.***'
                )
            if initial_file_content is not None:
                sections.append(
                    f"\n## Current content of {file_path}\n```\n"
                    + initial_file_content
                    + "\n```"
                )

        if tech_stack_content:
            sections.append("\n## Project tech stack (respect this)\n" + tech_stack_content)

        if file_listing:
            sections.append("\n## Other files in the workspace (for reference only)\n" + file_listing)

        if refinement_history:
            history_text = "\n".join(
                f"- [{r.get('status', '')}] {r.get('prompt', '')[:200]}..."
                for r in refinement_history[:5]
            )
            sections.append("\n## Previous refinement requests (do not undo these)\n" + history_text)

        # ── Instructions ───────────────────────────────────────────────
        if file_path:
            sections.append(f"""
## Instructions
Follow these steps exactly:
1. The content of **{file_path}** is provided above. Read it carefully.
2. If the user wants to DELETE or REMOVE this file: call file_deleter with file_path="{file_path}". Do NOT use file_writer to empty the file — use file_deleter so the file is removed from the filesystem. Then provide a Final Answer.
3. Otherwise, apply the user's requested changes to the content and call file_writer with file_path="{file_path}" and the COMPLETE updated file content (entire file, not a diff).
4. Provide a Final Answer summarizing what you changed or deleted.

CRITICAL RULES:
- ONLY modify or delete **{file_path}**. Do NOT touch other files.
- To delete this file: use file_deleter. To update it: use file_writer with COMPLETE content.
- You MUST call file_writer or file_deleter — without it, nothing is saved.
- Write the COMPLETE file content when using file_writer (not just the changed parts).
- If the user's request does not apply to this file, call file_writer anyway with the original content unchanged and explain in your Final Answer.""")
        else:
            sections.append("""
## Instructions
1. Call file_lister(".") to discover all files in the project.
2. Use file_reader to read each relevant source file.
3. If the user asked to DELETE or REMOVE files: for each file to remove, call file_deleter with that file_path. Do NOT empty files with file_writer — use file_deleter to remove them.
4. For files to modify (not delete): apply the user's changes and call file_writer for EACH with the COMPLETE updated content.
5. Provide a Final Answer listing all files you modified or deleted.

CRITICAL: Use file_deleter to remove files; use file_writer to update files. Without calling the right tool, nothing is saved.""")

        return "\n".join(sections)

    def run(
        self,
        user_prompt: str,
        file_path: Optional[str] = None,
        tech_stack_content: Optional[str] = None,
        file_listing: Optional[str] = None,
        refinement_history: Optional[List[Dict[str, Any]]] = None,
        initial_file_content: Optional[str] = None,
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
        prompt = self.build_prompt(
            user_prompt=user_prompt,
            file_path=file_path,
            tech_stack_content=tech_stack_content,
            file_listing=file_listing,
            refinement_history=refinement_history,
            initial_file_content=initial_file_content,
        )
        return str(self.agent.chat(prompt))
