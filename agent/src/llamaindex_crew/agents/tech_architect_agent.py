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
    
    def generate_api_contract(
        self,
        tech_stack: str,
        design_spec: str,
        user_stories: str = "",
    ) -> str:
        """Generate an OpenAPI 3.0 contract for fullstack projects.

        This is a **second pass** after the tech stack is defined.  It reads
        the design spec, user stories, and tech stack to produce a
        language-agnostic ``api_contract.yaml`` that both the backend and
        frontend agents code against.

        Args:
            tech_stack: Contents of tech_stack.md
            design_spec: Contents of design_spec.md
            user_stories: Contents of user_stories.md (optional)

        Returns:
            Agent response text
        """
        prompt = load_prompt(
            'tech_architect/generate_api_contract_task.txt',
            fallback=_DEFAULT_CONTRACT_PROMPT,
        ).format(
            tech_stack=tech_stack,
            design_spec=design_spec,
            user_stories=user_stories or "(none provided)",
        )

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


_DEFAULT_CONTRACT_PROMPT = """\
You are the Technical Architect.  The technology stack and file structure have
already been decided.  Now you must define the **API contract** between the
backend and frontend.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
INPUTS (read carefully)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Technology Stack:
{tech_stack}

Design Specification:
{design_spec}

User Stories:
{user_stories}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TASK — Generate api_contract.yaml
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Write a COMPLETE OpenAPI 3.0 specification that defines every REST endpoint the
frontend needs to call.  The contract must be **language-agnostic** — it
describes HTTP paths, methods, request bodies, response schemas, and status
codes without referencing any framework.

RULES:
1. Use OpenAPI 3.0.3 format (YAML).
2. Every entity in the design spec MUST have CRUD endpoints unless the design
   explicitly says otherwise.
3. Define ``components/schemas`` for every request and response object.
4. Include ``operationId`` for each operation (camelCase, e.g. ``listTodos``).
5. Use path parameters for resource identifiers, e.g. ``/todos/{{id}}``.
6. Include appropriate HTTP status codes (200, 201, 204, 400, 404, 500).
7. Add a brief ``description`` to each endpoint.
8. Do NOT include authentication/authorization details unless the design spec
   explicitly requires them.
9. Do NOT reference any framework (Flask, Spring, Express, etc.) — the
   contract is neutral.

ACTION REQUIRED:
Call file_writer(file_path='api_contract.yaml', content='<your OpenAPI spec>')
WAIT FOR: "✅ Successfully wrote to api_contract.yaml"

Your response should be:
"✅ Created api_contract.yaml with [N] endpoints covering [entities]"
"""
