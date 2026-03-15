"""
Tech Architect Agent - Defines technology stack
Migrated from TechArchitectCrew to LlamaIndex agent
"""
import logging
from pathlib import Path
from typing import Optional, Union
from .base_agent import BaseLlamaIndexAgent
from ..tools import FileWriterTool, FileReaderTool, create_workspace_file_tools
from ..utils.prompt_loader import load_prompt

logger = logging.getLogger(__name__)


class TechArchitectAgent:
    """Tech Architect Agent for defining technology stack"""
    
    def __init__(
        self,
        custom_backstory: Optional[str] = None,
        budget_tracker=None,
        workspace_path: Optional[Union[str, Path]] = None,
    ):
        """
        Initialize Tech Architect Agent

        Args:
            custom_backstory: Optional custom backstory (from Meta Agent)
            budget_tracker: Optional budget tracker instance
            workspace_path: When set, file tools write to this path (avoids thread-local/env issues).
        """
        default_backstory = load_prompt(
            'tech_architect/tech_architect_backstory.txt',
            fallback="""You are a Technical Architect.
Your goal is to translate logical designs into concrete technical decisions.
You select specific technology stacks, enforce technical standards, and identify architectural risks.
You consider the project vision and constraints when making decisions."""
        )
        
        backstory = custom_backstory or default_backstory

        if workspace_path is not None:
            ws_tools = create_workspace_file_tools(Path(workspace_path))
            tools = [ws_tools[0], ws_tools[1]]  # file_writer, file_reader
        else:
            tools = [FileWriterTool, FileReaderTool]
        
        self.agent = BaseLlamaIndexAgent(
            role="Technical Architect",
            goal="Select tech stack and define technical standards",
            backstory=backstory,
            tools=tools,
            agent_type="manager",
            budget_tracker=budget_tracker,
            verbose=True
        )
    
    def define_tech_stack(
        self,
        design_spec: str,
        vision: str,
        context_digest: Optional[str] = None
    ) -> str:
        """
        Define technology stack based on design specification
        
        Args:
            design_spec: Design specification content
            vision: Project vision
            context_digest: Optional Project Context Digest
        
        Returns:
            Result message
        """
        # Load task prompt
        task_prompt = load_prompt(
            'tech_architect/define_tech_stack_task.txt',
            fallback="""Review the design specification and define the concrete technology stack.

Design Specification: {design_spec}
Project Context: {context_digest}
Project Vision: {vision}

Select specific technologies (databases, frameworks, infrastructure) with justification.
Save to tech_stack.md"""
        )
        
        # Format prompt
        if context_digest:
            prompt = task_prompt.format(
                design_spec=design_spec,
                context_digest=context_digest,
                vision=vision
            )
        else:
            prompt = task_prompt.format(
                design_spec=design_spec,
                context_digest="",
                vision=vision
            )
        
        # Execute agent
        response = self.agent.chat(prompt)
        
        return str(response)
    
    def run(self, design_spec: str, vision: str, context_digest: Optional[str] = None) -> str:
        """
        Run the Tech Architect agent workflow
        
        Args:
            design_spec: Design specification content
            vision: Project vision
            context_digest: Optional Project Context Digest
        
        Returns:
            Result message
        """
        return self.define_tech_stack(design_spec, vision, context_digest)
