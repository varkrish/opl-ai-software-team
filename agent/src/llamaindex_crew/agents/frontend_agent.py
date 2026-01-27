"""
Frontend Agent - Implements UI components
Migrated from FrontendCrew to LlamaIndex agent
"""
import logging
from typing import Optional
from .base_agent import BaseLlamaIndexAgent
from ..tools import FileWriterTool, FileReaderTool, FileListTool
from ..utils.prompt_loader import load_prompt

logger = logging.getLogger(__name__)


class FrontendAgent:
    """Frontend Agent for implementing UI components"""
    
    def __init__(self, custom_backstory: Optional[str] = None, budget_tracker=None):
        """
        Initialize Frontend Agent
        
        Args:
            custom_backstory: Optional custom backstory (from Meta Agent)
            budget_tracker: Optional budget tracker instance
        """
        default_backstory = load_prompt(
            'frontend_crew/frontend_developer_backstory.txt',
            fallback="""You are a Frontend Developer.
Your goal is to implement user interfaces following design system principles.
You create reusable components and ensure responsive design."""
        )
        
        backstory = custom_backstory or default_backstory
        
        tools = [
            FileWriterTool,
            FileReaderTool,
            FileListTool
        ]
        
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
        user_stories: Optional[str] = None
    ) -> str:
        """
        Implement UI components based on design specification
        
        Args:
            design_spec: Design specification content
            tech_stack: Tech stack content
            user_stories: Optional user stories content
        
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
        # The prompt file expects 'requirements' and 'design_specs'
        prompt = task_prompt.format(
            design_spec=design_spec,
            design_specs=design_spec,
            tech_stack=tech_stack,
            user_stories=user_stories or "",
            requirements=user_stories or ""
        )
        
        # Execute agent
        response = self.agent.chat(prompt)
        
        return str(response)
    
    def run(self, design_spec: str, tech_stack: str, user_stories: Optional[str] = None) -> str:
        """
        Run the Frontend agent workflow
        
        Args:
            design_spec: Design specification content
            tech_stack: Tech stack content
            user_stories: Optional user stories content
        
        Returns:
            Result message
        """
        return self.implement_ui(design_spec, tech_stack, user_stories)
