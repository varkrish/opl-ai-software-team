"""
Designer Agent - Creates high-level design specifications
Migrated from DesignerCrew to LlamaIndex agent
"""
import logging
from typing import Optional
from .base_agent import BaseLlamaIndexAgent
from ..tools import FileWriterTool
from ..utils.prompt_loader import load_prompt

logger = logging.getLogger(__name__)


class DesignerAgent:
    """Designer Agent for creating logical architecture"""
    
    def __init__(self, custom_backstory: Optional[str] = None, budget_tracker=None):
        """
        Initialize Designer Agent
        
        Args:
            custom_backstory: Optional custom backstory (from Meta Agent)
            budget_tracker: Optional budget tracker instance
        """
        default_backstory = load_prompt(
            'designer/high_level_designer_backstory.txt',
            fallback="""You are a High-Level Design Agent.
Your goal is to design logical architecture without committing to specific technologies.
You use Domain-Driven Design (DDD), identify Bounded Contexts, define Data Flow and Domain Events.
You create C4 Model diagrams and define component capabilities."""
        )
        
        backstory = custom_backstory or default_backstory
        
        self.agent = BaseLlamaIndexAgent(
            role="High-Level Designer",
            goal="Design logical architecture and system boundaries",
            backstory=backstory,
            tools=[FileWriterTool],
            agent_type="manager",
            budget_tracker=budget_tracker,
            verbose=True
        )
    
    def create_design_spec(
        self,
        user_stories: str,
        context_digest: Optional[str] = None
    ) -> str:
        """
        Create design specification based on user stories
        
        Args:
            user_stories: User stories content
            context_digest: Optional Project Context Digest
        
        Returns:
            Result message
        """
        # Load task prompt
        task_prompt = load_prompt(
            'designer/create_design_spec_task.txt',
            fallback="""Design the logical architecture for the user stories.

User Stories: {user_stories}
Project Context: {context_digest}

Create design specification with bounded contexts, data flow, domain events, and component diagrams.
Save to design_spec.md"""
        )
        
        # Format prompt
        if context_digest:
            prompt = task_prompt.format(user_stories=user_stories, context_digest=context_digest)
        else:
            prompt = task_prompt.format(user_stories=user_stories, context_digest="")
        
        # Execute agent
        response = self.agent.chat(prompt)
        
        return str(response)
    
    def run(self, user_stories: str, context_digest: Optional[str] = None) -> str:
        """
        Run the Designer agent workflow
        
        Args:
            user_stories: User stories content
            context_digest: Optional Project Context Digest
        
        Returns:
            Result message
        """
        return self.create_design_spec(user_stories, context_digest)
