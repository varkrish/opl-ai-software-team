"""
Frontend Agent - Implements UI components
Migrated from FrontendCrew to LlamaIndex agent
"""
import logging
from pathlib import Path
from typing import Optional
from .base_agent import BaseLlamaIndexAgent
from ..tools import FileWriterTool, FileReaderTool, FileListTool, create_workspace_file_tools
from ..tools.tool_loader import load_tools
from ..config import ConfigLoader
from ..utils.prompt_loader import load_prompt

logger = logging.getLogger(__name__)


class FrontendAgent:
    """Frontend Agent for implementing UI components"""
    
    def __init__(
        self,
        custom_backstory: Optional[str] = None,
        budget_tracker=None,
        workspace_path: Optional[Path] = None,
    ):
        """
        Initialize Frontend Agent

        Args:
            custom_backstory: Optional custom backstory (from Meta Agent)
            budget_tracker: Optional budget tracker instance
            workspace_path: When set, file tools write to this path (avoids thread-local/env issues).
        """
        default_backstory = load_prompt(
            'frontend_crew/frontend_developer_backstory.txt',
            fallback="""You are a Frontend Developer.
Your goal is to implement user interfaces following design system principles.
You create reusable components and ensure responsive design."""
        )
        
        backstory = custom_backstory or default_backstory

        if workspace_path is not None:
            ws_tools = create_workspace_file_tools(Path(workspace_path))
            tools = [ws_tools[0], ws_tools[1], ws_tools[2]]
        else:
            tools = [FileWriterTool, FileReaderTool, FileListTool]

        try:
            config = ConfigLoader.load()
            entries = config.tools.global_tools + config.tools.agent_tools.get("frontend", [])
            extra_tools = load_tools(entries)
            tools.extend(extra_tools)
            logger.info("FrontendAgent: loaded %d extra tool(s) from config", len(extra_tools))
        except Exception:
            logger.warning("FrontendAgent: failed to load extra tools from config — continuing with built-ins", exc_info=True)

        self.agent = BaseLlamaIndexAgent(
            role="Frontend Developer",
            goal="Implement UI components and user interfaces",
            backstory=backstory,
            tools=tools,
            agent_type="worker",
            budget_tracker=budget_tracker,
            verbose=True
        )
    
    def implement_ui(
        self,
        design_spec: str,
        tech_stack: str,
        user_stories: Optional[str] = None,
        vision: Optional[str] = None,
    ) -> str:
        """
        Implement UI components based on design specification
        
        Args:
            design_spec: Design specification content
            tech_stack: Tech stack content
            user_stories: Optional user stories content
            vision: Original project vision (anchors implementation to user intent)
        
        Returns:
            Result message
        """
        # Load task prompt
        task_prompt = load_prompt(
            'frontend_crew/implement_ui_task.txt',
            fallback="""Implement UI components based on the design specification.

Design Specification: {design_spec}
Tech Stack: {tech_stack}
User Stories: {user_stories}

Create all required UI components and ensure they match the design spec.
Save files to src/ directory."""
        )
        
        # Format prompt
        prompt = task_prompt.format(
            design_spec=design_spec,
            design_specs=design_spec,
            tech_stack=tech_stack,
            user_stories=user_stories or "",
            requirements=user_stories or ""
        )

        if vision:
            prompt = (
                f"ORIGINAL PROJECT VISION (this is the ground truth — your code MUST implement this):\n"
                f"{vision}\n\n{prompt}"
            )
        
        # Execute agent
        response = self.agent.chat(prompt)
        
        return str(response)
    
    def run(self, design_spec: str, tech_stack: str, user_stories: Optional[str] = None,
            vision: Optional[str] = None) -> str:
        """
        Run the Frontend agent workflow
        
        Args:
            design_spec: Design specification content
            tech_stack: Tech stack content
            user_stories: Optional user stories content
            vision: Original project vision
        
        Returns:
            Result message
        """
        return self.implement_ui(design_spec, tech_stack, user_stories, vision=vision)
