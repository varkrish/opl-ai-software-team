"""
Tech Architect Agent - Defines technology stack
Migrated from TechArchitectCrew to LlamaIndex agent
"""
import logging
from pathlib import Path
from typing import Optional, Union

from .base_agent import BaseLlamaIndexAgent
from ..tools import FileWriterTool, FileReaderTool, create_workspace_file_tools, prefetch_skills, append_tldr_tools
from ..tools.tool_loader import load_tools
from ..config import ConfigLoader
from ..utils.prompt_loader import load_prompt
from ..utils.prompt_budget import PromptBudget

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
        self.workspace_path = Path(workspace_path) if workspace_path else None

        default_backstory = load_prompt(
            'tech_architect/tech_architect_backstory.txt',
            fallback="""You are a Technical Architect.
Your goal is to translate logical designs into concrete technical decisions.
You select specific technology stacks, enforce technical standards, and identify architectural risks.
You consider the project vision and constraints when making decisions."""
        )
        
        backstory = custom_backstory or default_backstory

        if workspace_path is not None:
            ws_tools = create_workspace_file_tools(self.workspace_path)
            tools = [ws_tools[0], ws_tools[1]]  # file_writer, file_reader
            append_tldr_tools(tools, self.workspace_path)
        else:
            tools = [FileWriterTool, FileReaderTool]

        try:
            config = ConfigLoader.load()
            entries = config.tools.global_tools + config.tools.agent_tools.get("tech_architect", [])
            extra_tools = load_tools(entries)
            tools.extend(extra_tools)
            if extra_tools:
                backstory += (
                    "\n\nFramework-specific skills are automatically injected into your task "
                    "prompt as FRAMEWORK REFERENCE. Your tech stack and file structure MUST "
                    "follow the conventions described there. Do NOT invent folder structures "
                    "or patterns — use what the skill reference shows."
                )
            logger.info("TechArchitectAgent: loaded %d extra tool(s) from config", len(extra_tools))
        except Exception:
            logger.warning("TechArchitectAgent: failed to load extra tools — continuing with built-ins", exc_info=True)

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
        context_digest: Optional[str] = None,
        reference_context: Optional[str] = None,
    ) -> str:
        """
        Define technology stack based on design specification
        
        Args:
            design_spec: Design specification content
            vision: Project vision
            context_digest: Optional Project Context Digest
            reference_context: Optional RAG-retrieved reference excerpts
        
        Returns:
            Result message
        """
        skill_context = prefetch_skills(
            vision=vision,
            role="tech_architect",
            workspace_path=self.workspace_path,
        )

        task_prompt = load_prompt(
            'tech_architect/define_tech_stack_task.txt',
            fallback=_DEFAULT_TECH_STACK_PROMPT,
        )

        # Fit sections into the model's context window; protect vision and design_spec first
        budget = PromptBudget.from_llm(self.agent.llm)
        sections = {
            "design_spec":    design_spec or "",
            "context_digest": context_digest or "",
            "vision":         vision or "",
            "skill_context":  skill_context or "",
        }
        ref_overhead = len(reference_context) if reference_context and reference_context.strip() else 0
        fixed_overhead = len(task_prompt) + ref_overhead
        trimmed = budget.fit(
            sections,
            fixed_overhead_chars=fixed_overhead,
            priority=["vision", "design_spec", "context_digest", "skill_context"],
        )

        prompt = task_prompt.format(**trimmed)
        if reference_context and reference_context.strip():
            prompt = (
                f"REFERENCE DOCUMENT EXCERPTS (retrieved — follow file structure and constraints):\n"
                f"{reference_context.strip()}\n\n{prompt}"
            )

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

    def run(
        self,
        design_spec: str,
        vision: str,
        context_digest: Optional[str] = None,
        reference_context: Optional[str] = None,
    ) -> str:
        """
        Run the Tech Architect agent workflow
        
        Args:
            design_spec: Design specification content
            vision: Project vision
            context_digest: Optional Project Context Digest
            reference_context: Optional RAG-retrieved reference excerpts
        
        Returns:
            Result message
        """
        return self.define_tech_stack(
            design_spec, vision, context_digest, reference_context=reference_context,
        )


_DEFAULT_TECH_STACK_PROMPT = """\
You are the Technical Architect. Define the concrete technology stack for this project.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
INPUTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Design Specification:
{design_spec}

Project Context:
{context_digest}

Project Vision:
{vision}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FRAMEWORK REFERENCE (GROUND TRUTH — you MUST follow this)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

The following skill documents describe the REAL conventions for the target framework.
Your file structure and coding patterns MUST match these exactly.
Do NOT invent folders, files, or patterns that are not shown here.

{skill_context}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TASK — Define Tech Stack
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Based on the FRAMEWORK REFERENCE above and the design specification:

1. Select specific technologies (database, framework, infrastructure) with justification.
2. The file structure MUST be copied from the FRAMEWORK REFERENCE skill documents above.
   Do NOT use generic MVC patterns (models/, controllers/, views/) unless the framework
   reference explicitly shows them.
3. For Frappe apps: the structure comes from `bench new-app`. DocTypes are defined by
   JSON files, not Python model classes. There are no migrations/ folders.
4. List every file the developer agents need to create, using the exact folder layout
   from the skill reference.
5. You MUST enumerate concrete filenames with extensions (e.g., Task.java, UserService.java). NEVER list only folders (e.g., controller/, service/) without the files inside them. The orchestrator cannot create tasks for folder names.

Call file_writer(file_path='tech_stack.md', content='<your tech stack>')

Your final response MUST be formatted as:
Thought: I have successfully created the tech_stack.md file.
Final Answer: ✅ Created complete tech_stack.md with buildable [technology] structure
"""


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

Your final response MUST be formatted as:
Thought: I have successfully created the api_contract.yaml file.
Final Answer: ✅ Created api_contract.yaml with [N] endpoints covering [entities]
"""
