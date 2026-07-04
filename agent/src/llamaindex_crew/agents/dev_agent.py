"""
Development Agent - Implements features using TDD
Migrated from DevCrew to LlamaIndex agent
"""
import logging
from pathlib import Path
from typing import Optional, List
from .base_agent import BaseLlamaIndexAgent
from ..tools import (
    FileWriterTool, BulkFileWriterTool, FileReaderTool, FileListTool,
    GitTool,
    PytestRunnerTool, CodeCoverageTool,
    create_workspace_file_tools,
    append_tldr_tools,
)
from ..tools.tool_loader import load_tools
from ..config import ConfigLoader
from ..utils.prompt_loader import load_prompt
from ..utils.llm_config import get_supports_react

logger = logging.getLogger(__name__)


class DevAgent:
    """Development Agent for implementing features using TDD"""
    
    def __init__(
        self,
        custom_backstory: Optional[str] = None,
        budget_tracker=None,
        workspace_path: Optional[Path] = None,
        config=None,
    ):
        """
        Initialize Development Agent

        Args:
            custom_backstory: Optional custom backstory (from Meta Agent)
            budget_tracker: Optional budget tracker instance
            workspace_path: When set, file tools write to this path (avoids thread-local/env issues).
        """
        default_backstory = load_prompt(
            'dev_crew/developer_backstory.txt',
            fallback="""You are a Developer.
Your goal is to implement features using Test-Driven Development (TDD).
You practice Horizontal Slicing: Database -> API -> Frontend.
You verify and use the technology stack defined by the Technical Architect."""
        )

        backstory = custom_backstory or default_backstory

        # Determine capability mode for the worker model
        self.supports_react = get_supports_react("worker")
        logger.info("DevAgent: supports_react=%s", self.supports_react)

        tool_config = config
        if tool_config is None:
            try:
                tool_config = ConfigLoader.load()
            except Exception:
                tool_config = None

        if self.supports_react:
            if workspace_path is not None:
                ws_tools = create_workspace_file_tools(Path(workspace_path))
                tools = list(ws_tools) + [GitTool, PytestRunnerTool, CodeCoverageTool]
                append_tldr_tools(tools, Path(workspace_path), config=tool_config)
            else:
                tools = [
                    FileWriterTool, BulkFileWriterTool, FileReaderTool, FileListTool,
                    GitTool, PytestRunnerTool, CodeCoverageTool,
                ]
        else:
            # Simple mode: no tools — single-shot JSON output; workflow writes files
            # via output_parser.  Context is already injected in build_file_prompt.
            tools = []

        try:
            cfg = tool_config or ConfigLoader.load()
            entries = cfg.tools.global_tools + cfg.tools.agent_tools.get("developer", [])
            extra_tools = load_tools(entries)
            tools.extend(extra_tools)
            logger.info("DevAgent: loaded %d extra tool(s) from config", len(extra_tools))
        except Exception:
            logger.warning("DevAgent: failed to load extra tools from config — continuing with built-ins", exc_info=True)

        self.agent = BaseLlamaIndexAgent(
            role="Developer",
            goal="Implement features using TDD and horizontal slicing",
            backstory=backstory,
            tools=tools,
            agent_type="worker",
            budget_tracker=budget_tracker,
            verbose=True
        )
    
    def implement_features(
        self,
        features: List[str],
        tech_stack: str,
        user_stories: Optional[str] = None
    ) -> str:
        """
        Implement features using TDD
        
        Args:
            features: List of feature names or descriptions
            tech_stack: Tech stack content
            user_stories: Optional user stories content
        
        Returns:
            Result message
        """
        # Load task prompt
        task_prompt = load_prompt(
            'dev_crew/implement_feature.txt',
            fallback="""Implement features using Test-Driven Development (TDD).

Features to implement: {features}
Tech Stack: {tech_stack}
User Stories: {user_stories}

Follow TDD cycle: Red -> Green -> Refactor.
Create implementation files before test files.
Use horizontal slicing approach."""
        )
        
        features_str = "\n".join(f"- {f}" for f in features)
        
        # Format prompt
        # The prompt file expects 'feature_desc' and 'feature_context'
        prompt = task_prompt.format(
            features=features_str,
            feature_desc=features_str,
            tech_stack=tech_stack,
            user_stories=user_stories or "",
            feature_context=f"Tech Stack: {tech_stack}\nUser Stories: {user_stories or ''}"
        )
        
        # Execute agent
        response = self.agent.chat(prompt)
        
        return str(response)
    
    def run(self, features: List[str], tech_stack: str, user_stories: Optional[str] = None) -> str:
        """
        Run the Development agent workflow
        
        Args:
            features: List of feature names
            tech_stack: Tech stack content
            user_stories: Optional user stories content
        
        Returns:
            Result message
        """
        return self.implement_features(features, tech_stack, user_stories)
