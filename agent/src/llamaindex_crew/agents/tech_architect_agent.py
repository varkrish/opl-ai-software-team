"""
Tech Architect Agent - Defines technology stack
Migrated from TechArchitectCrew to LlamaIndex agent
"""
import logging
from pathlib import Path
from typing import Optional, Union

from .base_agent import BaseLlamaIndexAgent
from ..tools import FileWriterTool, FileReaderTool, create_workspace_file_tools, prefetch_skills
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

        # The Tech Architect explicitly uses XML output tags (<tech_stack>, <implementation_plan>) 
        # and its output is parsed by the Python wrapper. We do NOT want to give it any tools.
        # Giving it tools forces the ReActAgent parser, which fails on large trimmed contexts.
        tools = []

        try:
            config = ConfigLoader.load()
            entries = config.tools.global_tools + config.tools.agent_tools.get("tech_architect", [])
            extra_tools = load_tools(entries)
            # DO NOT append extra tools to force SimpleAgent usage!
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

        # When no skill cleared the relevance threshold (e.g. the framework in the
        # vision has no indexed skill — Camel, Angular, etc.), do NOT leave the
        # "FRAMEWORK REFERENCE (GROUND TRUTH)" section blank. An empty section
        # under a "you MUST follow this" heading is confusing; be explicit that
        # there is no framework-specific reference and the model must rely on
        # standard, well-known conventions for the requested technology instead.
        if not (skill_context or "").strip():
            skill_context = (
                "(No indexed skill matched this project's technology closely enough to be "
                "trustworthy — none was injected.) Use the well-established, standard "
                "conventions for the EXACT technology named in the vision. Do NOT default to "
                "a different framework's patterns (e.g. do NOT use Spring MVC-style "
                "controllers/services for a framework that has no such concept, such as "
                "Apache Camel route builders, event-driven systems, or ESB-style integration)."
            )

        # Fit sections into the model's context window; protect vision and design_spec first
        budget = PromptBudget.from_llm(self.agent.llm)
        sections = {
            "design_spec":    design_spec or "",
            "context_digest": context_digest or "",
            "vision":         vision or "",
            "skill_context":  skill_context,
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
        response_text = str(response)
        
        # DeepSeek-R1 outputs the final files inside XML tags to avoid JSON serialization failures
        # Extract them and write to disk
        if self.workspace_path:
            import re
            tech_stack_path = self.workspace_path / "tech_stack.md"
            impl_plan_path = self.workspace_path / "implementation_plan.md"
            
            tech_match = re.search(r'<tech_stack>\s*(.*?)\s*</tech_stack>', response_text, re.DOTALL)
            if tech_match:
                tech_stack_path.write_text(tech_match.group(1).strip())
            elif not tech_stack_path.exists():
                logger.warning("tech_stack.md not found and XML tags missing. Applying fallback.")
                tech_stack_path.write_text(response_text)
                
            impl_match = re.search(r'<implementation_plan>\s*(.*?)\s*</implementation_plan>', response_text, re.DOTALL)
            if impl_match:
                impl_plan_path.write_text(impl_match.group(1).strip())
            elif not impl_plan_path.exists():
                logger.warning("implementation_plan.md not found and XML tags missing. Applying fallback.")
                impl_plan_path.write_text(response_text)

        return response_text
    
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
You are the Technical Architect. Define the concrete technology stack and the logical implementation plan for this project.

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
TASK 1 — Define Tech Stack
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Based on the FRAMEWORK REFERENCE above and the design specification:

1. Select specific technologies (database, framework, infrastructure) with justification.
2. The file structure MUST be copied from the FRAMEWORK REFERENCE skill documents above
   when one is present. Do NOT use generic MVC patterns (models/, controllers/, views/)
   unless the framework reference explicitly shows them OR the EXACT technology named in
   the vision is itself an MVC framework (e.g. Spring MVC, Django, Rails). When no skill
   reference is present, use the well-known standard conventions of the named technology
   instead of defaulting to MVC — e.g. Apache Camel uses Route/Processor classes, not
   controllers/services; event-driven systems use handlers/consumers, not controllers.
3. For Frappe apps: the structure comes from `bench new-app`. DocTypes are defined by
   JSON files, not Python model classes. There are no migrations/ folders.
4. List every file the developer agents need to create, using the exact folder layout
   from the skill reference.
5. You MUST enumerate concrete filenames with extensions using names specific to the
   entities in the design spec (e.g., a Task entity in Apache Camel → routes/TaskRoute.java;
   in Spring MVC → controller/TaskController.java + service/TaskService.java). NEVER list
   only folders (e.g., controller/, service/, routes/) without the concrete files inside
   them. The orchestrator cannot create tasks for folder names.

Call file_writer(file_path='tech_stack.md', content='<your tech stack>')

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TASK 2 — Write Logical Implementation Plan
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

After creating `tech_stack.md`, you MUST write a comprehensive logical implementation plan to `implementation_plan.md` detailing the technical architecture and logical solution rather than just listing files.
The plan MUST contain:
1. **Architectural Overview**: Logical system architecture and core design patterns (e.g. clean architecture, service layers, websocket handshake details, real-time message broadcasting).
2. **Core Logical Components**: Description of how core business logic, database tables, socket handlers, and event emitters function logically.
3. **Data Flow & Sequence**: Textual explanation or Mermaid diagrams showing request-response or message transmission paths through routing, controllers, services, database/socket, and client response.
4. **Integration Strategy**: How the frontend integrates with the backend API, state management of socket connections, and reconnection loops.
5. **Security, Validation & Error Handling**: Authentication/authorization enforcement (e.g. token extraction, RBAC, channel auth), inputs validation rules, rate-limiting, and resilience features for connection dropouts.
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
