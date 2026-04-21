"""
Tech Architect Agent - Defines technology stack
Migrated from TechArchitectCrew to LlamaIndex agent
"""
import logging
from pathlib import Path
from typing import Optional, Union

import httpx

from .base_agent import BaseLlamaIndexAgent
from ..tools import FileWriterTool, FileReaderTool, create_workspace_file_tools
from ..tools.tool_loader import load_tools
from ..config import ConfigLoader
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

        try:
            config = ConfigLoader.load()
            entries = config.tools.global_tools + config.tools.agent_tools.get("tech_architect", [])
            extra_tools = load_tools(entries)
            tools.extend(extra_tools)
            if extra_tools:
                backstory += (
                    "\n\nYou have access to a skill_query tool. ALWAYS use it before defining the "
                    "tech stack to search for framework-specific skills and coding patterns "
                    "(e.g. 'Frappe app architecture', 'Frappe DocType patterns', 'custom app development'). "
                    "This ensures your tech stack decisions align with the target framework's conventions."
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
    
    @staticmethod
    def _prefetch_skills(vision: str) -> str:
        """Pre-fetch framework-specific skills to inject as ground truth."""
        try:
            config = ConfigLoader.load()
            url = getattr(config, 'skills', None)
            service_url = getattr(url, 'service_url', None) if url else None
            if not service_url:
                return ""

            queries = [
                f"{vision} app folder structure scaffold conventions",
                f"{vision} DocType patterns architecture",
            ]
            sections: list[str] = []
            for q in queries:
                resp = httpx.post(
                    f"{service_url}/query",
                    json={"query": q, "top_k": 3},
                    timeout=15,
                )
                resp.raise_for_status()
                for r in resp.json().get("results", []):
                    sections.append(f"[Skill: {r['skill_name']}]\n{r['content']}")

            if sections:
                logger.info("TechArchitectAgent: pre-fetched %d skill sections", len(sections))
                return "\n\n---\n\n".join(sections)
        except Exception:
            logger.warning("TechArchitectAgent: skill pre-fetch failed", exc_info=True)
        return ""

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
        skill_context = self._prefetch_skills(vision)

        task_prompt = load_prompt(
            'tech_architect/define_tech_stack_task.txt',
            fallback=_DEFAULT_TECH_STACK_PROMPT,
        )

        prompt = task_prompt.format(
            design_spec=design_spec,
            context_digest=context_digest or "",
            vision=vision,
            skill_context=skill_context,
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

Call file_writer(file_path='tech_stack.md', content='<your tech stack>')
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

Your response should be:
"✅ Created api_contract.yaml with [N] endpoints covering [entities]"
"""
