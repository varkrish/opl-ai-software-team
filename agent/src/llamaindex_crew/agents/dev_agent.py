"""
Development Agent - Implements features using TDD
Migrated from DevCrew to LlamaIndex agent
"""
import logging
from typing import Optional, List
from .base_agent import BaseLlamaIndexAgent
from ..tools import (
    FileWriterTool, FileReaderTool, FileListTool,
    GitInitTool, GitCommitTool, GitStatusTool,
    PytestRunnerTool, CodeCoverageTool
)
from ..utils.prompt_loader import load_prompt

logger = logging.getLogger(__name__)


class DevAgent:
    """Development Agent for implementing features using TDD"""
    
    def __init__(self, custom_backstory: Optional[str] = None, budget_tracker=None):
        """
        Initialize Development Agent
        
        Args:
            custom_backstory: Optional custom backstory (from Meta Agent)
            budget_tracker: Optional budget tracker instance
        """
        default_backstory = load_prompt(
            'dev_crew/developer_backstory.txt',
            fallback="""You are a Developer.
Your goal is to implement features using Test-Driven Development (TDD).
You practice Horizontal Slicing: Database -> API -> Frontend.
You verify and use the technology stack defined by the Technical Architect."""
        )
        
        backstory = custom_backstory or default_backstory
        
        # All development tools
        tools = [
            FileWriterTool,
            FileReaderTool,
            FileListTool,
            GitInitTool,
            GitCommitTool,
            GitStatusTool,
            PytestRunnerTool,
            CodeCoverageTool
        ]
        
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
