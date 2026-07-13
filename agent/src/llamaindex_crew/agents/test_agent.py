"""
Test Agent — writes, runs, and fixes tests using workspace-bound tools and tldr search.
"""
import logging
from pathlib import Path
from typing import Optional, List

from .base_agent import BaseLlamaIndexAgent
from ..tools import (
    create_workspace_file_tools,
    PytestRunnerTool,
    CodeCoverageTool,
    append_tldr_tools,
)
from ..tools.tool_loader import load_tools
from ..utils.llm_config import get_supports_react
from ..config import ConfigLoader
from ..utils.prompt_loader import load_prompt

logger = logging.getLogger(__name__)

_TEST_BACKSTORY_FALLBACK = """You are a QA / Test Engineer.
Your goal is to create, run, and fix automated tests for the project.

Use code_structure and code_search to understand the codebase before writing tests.
Use code_impact to find callers when fixing failures that span multiple files.
Use pytest_runner to execute tests and code_coverage for coverage reports.
Use file_writer to create or update test files with complete file content."""


class TestAgent:
    """Agent focused on test authoring, execution, and remediation."""

    def __init__(
        self,
        workspace_path: Path,
        custom_backstory: Optional[str] = None,
        budget_tracker=None,
    ):
        self.workspace_path = Path(workspace_path)
        backstory = custom_backstory or load_prompt(
            "qa/test_engineer_backstory.txt",
            fallback=_TEST_BACKSTORY_FALLBACK,
        )

        tools = list(create_workspace_file_tools(self.workspace_path))
        tools.extend([PytestRunnerTool, CodeCoverageTool])
        append_tldr_tools(tools, self.workspace_path)

        try:
            config = ConfigLoader.load()
            entries = config.tools.global_tools + config.tools.agent_tools.get("test", [])
            extra_tools = load_tools(entries)
            tools.extend(extra_tools)
            logger.info("TestAgent: loaded %d extra tool(s) from config", len(extra_tools))
        except Exception:
            logger.warning(
                "TestAgent: failed to load extra tools from config — continuing with built-ins",
                exc_info=True,
            )

        self.agent = BaseLlamaIndexAgent(
            role="Test Engineer",
            goal="Author, run, and fix automated tests for the project",
            backstory=backstory,
            tools=tools,
            agent_type="worker",
            budget_tracker=budget_tracker,
            verbose=True,
        )
        self.supports_react = get_supports_react("worker")

    def run_tests(
        self,
        instructions: List[str],
        tech_stack: str = "",
        context: str = "",
    ) -> str:
        """Run test-related work from a list of instructions or failure summaries."""
        tasks = "\n".join(f"- {t}" for t in instructions)
        prompt = (
            f"Technology stack:\n{tech_stack or '(not specified)'}\n\n"
            f"Additional context:\n{context or '(none)'}\n\n"
            f"Tasks:\n{tasks}\n\n"
            "Use pytest_runner to verify fixes. Use tldr tools to explore code before editing."
        )
        return str(self.agent.chat(prompt))

    def run(
        self,
        instructions: List[str],
        tech_stack: str = "",
        context: str = "",
    ) -> str:
        return self.run_tests(instructions, tech_stack=tech_stack, context=context)
