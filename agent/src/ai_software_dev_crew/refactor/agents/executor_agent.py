"""
Refactor Executor Agent
Executes a single refactor task: apply instruction, write refactored output.
For modify: receives original content from runner (no copy of old code into refactored/).
"""
from typing import Optional

from agent.src.llamaindex_crew.agents.base_agent import BaseLlamaIndexAgent
from agent.src.llamaindex_crew.tools.file_tools import create_workspace_file_tools


class RefactorExecutorAgent(BaseLlamaIndexAgent):
    """Executes one refactor task (file_path + instruction) using file write tools."""

    def __init__(self, workspace_path: str, job_id: str):
        self.workspace_path = workspace_path

        # Tools: Read/Write files (read for listing/context; write for output)
        tools = create_workspace_file_tools(workspace_path)

        super().__init__(
            role="Refactor Developer",
            goal="Apply specific code changes defined in the plan",
            backstory=(
                "You are a skilled Software Developer. "
                "You receive a specific instruction to refactor a file. "
                "You produce the refactored version and write it to the workspace. "
                "You do not copy old code as-is; you output only the new refactored code."
            ),
            tools=tools,
            agent_type="worker",
            verbose=True
        )

    def execute_task(
        self,
        file_path: str,
        instruction: str,
        action: str = "modify",
        source_content: Optional[str] = None,
    ) -> str:
        # Threshold for switching to surgical edits
        IS_LARGE_THRESHOLD = 30000
        is_large = False
        if source_content and len(source_content) > IS_LARGE_THRESHOLD:
            is_large = True

        if action == "create":
            task_instructions = """
        1. Create the file from scratch per the instruction (do not read an existing file).
        2. Use file_writer to write the new file.
        3. Return a summary of what you created.
            """
            prompt = f"""
        EXECUTE REFACTOR (action={action})

        File: {file_path}
        Instruction: {instruction}

        Your tasks:
        {task_instructions}

        Reference target_architecture.md in the workspace for guidance if present.
        IMPORTANT: Use file_writer to save your changes.
        """
        elif is_large:
            # surgical edits for large files
            lines = source_content.splitlines()
            numbered_content = "\n".join(f"{i+1:4}: {line}" for i, line in enumerate(lines))
            
            task_instructions = """
        1. Identify the line ranges that need to change in the legacy code.
        2. Use `file_line_replacer` to make surgical changes to specific line ranges.
        3. Do NOT use `file_writer` as it may fail or truncate this large file.
        4. Return a summary of what you changed.
            """
            prompt = f"""
        EXECUTE REFACTOR (action={action}, LARGE FILE)

        File: {file_path}
        Instruction: {instruction}

        --- ORIGINAL (legacy) content to refactor (with line numbers) ---
        {numbered_content}
        --- END ORIGINAL ---

        Your tasks:
        {task_instructions}

        Reference target_architecture.md in the workspace for guidance if present.
        IMPORTANT: Use `file_line_replacer` to apply your changes surgically.
        """
        else:
            # Small file: use file_writer for full rewrite
            task_instructions = """
        1. Refactor the ORIGINAL content below according to the instruction.
        2. Write ONLY the refactored version to the file using file_writer (do not write the old code as-is).
        3. Return a summary of what you changed.
            """
            content_block = (
                f"\n\n--- ORIGINAL (legacy) content to refactor ---\n{source_content}\n--- END ORIGINAL ---\n"
                if source_content is not None
                else ""
            )
            prompt = f"""
        EXECUTE REFACTOR (action={action})

        File: {file_path}
        Instruction: {instruction}
        {content_block}

        Your tasks:
        {task_instructions}

        Reference target_architecture.md in the workspace for guidance if present.
        IMPORTANT: Use file_writer to save the refactored file. Do not copy the old code unchanged.
        """
        return str(self.chat(prompt))
