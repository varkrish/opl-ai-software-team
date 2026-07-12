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
from ..utils.vision_stack_analysis import (
    build_stack_selection_brief,
    extract_named_components,
    format_approved_solution_contract,
)

logger = logging.getLogger(__name__)


def _format_stack_manifest_section(workspace_path) -> str:
    if not workspace_path:
        return ""
    try:
        from ..workflows.solutioning_loop import read_stack_manifest
        manifest = read_stack_manifest(workspace_path)
    except Exception:
        return ""
    if not manifest:
        return ""
    import json
    return (
        "BINDING STACK MANIFEST (locked early — tech_stack.md MUST be consistent):\n"
        f"{json.dumps(manifest, indent=2)}\n"
        "Do NOT invent tiers listed in forbidden_tiers. "
        "Produce tech_stack.md + implementation_plan.md within these constraints."
    )


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

    # ------------------------------------------------------------------
    # Internal helpers shared by the three passes
    # ------------------------------------------------------------------

    def _prepare_context(
        self,
        design_spec: str,
        vision: str,
        context_digest: Optional[str],
        reference_context: Optional[str],
        user_stories: Optional[str],
        approved_solution: bool,
        solution_spec: Optional[str],
    ):
        """Build the shared context dict used across passes."""
        skill_context = prefetch_skills(
            vision=vision,
            role="tech_architect",
            workspace_path=self.workspace_path,
        )

        stack_brief = build_stack_selection_brief(
            vision or "",
            user_stories or "",
            approved_solution=approved_solution,
        )
        manifest_section = _format_stack_manifest_section(self.workspace_path)
        if manifest_section:
            stack_brief = f"{stack_brief}\n\n{manifest_section}"

        if approved_solution and not (solution_spec or "").strip() and self.workspace_path:
            spec_path = self.workspace_path / "solution_spec.md"
            if spec_path.exists():
                try:
                    solution_spec = spec_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    solution_spec = solution_spec or ""
        solution_contract = (
            format_approved_solution_contract(solution_spec or "")
            if approved_solution
            else ""
        )

        if not (skill_context or "").strip():
            skill_context = (
                "(No indexed skill matched this project's technology closely enough to be "
                "trustworthy — none was injected.) Infer the minimal appropriate stack from "
                "the STACK SELECTION BRIEF and vision. Use standard conventions for the "
                "chosen technology. Do NOT default to an unrelated application platform."
            )

        return {
            "design_spec": design_spec or "",
            "context_digest": context_digest or "",
            "vision": vision or "",
            "skill_context": skill_context,
            "stack_brief": stack_brief,
            "solution_contract": solution_contract,
            "solution_spec": solution_spec or "",
            "reference_context": reference_context or "",
            "approved_solution": approved_solution,
        }

    def _fit_and_format(self, ctx: dict, task_prompt: str) -> str:
        """Trim context to fit the model's window, then format the prompt."""
        budget = PromptBudget.from_llm(self.agent.llm)
        sections = {
            "design_spec": ctx["design_spec"],
            "context_digest": ctx["context_digest"],
            "vision": ctx["vision"],
            "skill_context": ctx["skill_context"],
            "stack_brief": ctx["stack_brief"],
            "solution_contract": ctx["solution_contract"],
        }
        ref = ctx.get("reference_context", "")
        ref_overhead = len(ref) if ref.strip() else 0
        fixed_overhead = len(task_prompt) + ref_overhead
        priority = (
            ["solution_contract", "stack_brief", "design_spec", "vision", "context_digest", "skill_context"]
            if ctx.get("approved_solution")
            else ["vision", "stack_brief", "design_spec", "context_digest", "skill_context"]
        )
        trimmed = budget.fit(sections, fixed_overhead_chars=fixed_overhead, priority=priority)
        prompt = task_prompt.format(**trimmed)
        if ref.strip():
            prompt = (
                f"REFERENCE DOCUMENT EXCERPTS (retrieved — follow constraints):\n"
                f"{ref.strip()}\n\n{prompt}"
            )
        return prompt

    @staticmethod
    def _extract_and_write(response_text: str, path: Path, xml_tag: str) -> None:
        """Extract content from <xml_tag>...</xml_tag> or fall back to full response."""
        import re
        match = re.search(rf'<{xml_tag}>\s*(.*?)\s*</{xml_tag}>', response_text, re.DOTALL)
        if match:
            path.write_text(match.group(1).strip(), encoding="utf-8")
        elif not path.exists():
            logger.warning("%s not found and <%s> tag missing. Writing full response.", path.name, xml_tag)
            path.write_text(response_text, encoding="utf-8")

    # ------------------------------------------------------------------
    # Pass 1 — Stack selection (technologies + justification, ~short)
    # ------------------------------------------------------------------

    def _pass1_stack_selection(self, ctx: dict, stack_correction: Optional[str]) -> str:
        prompt_template = _PASS1_STACK_SELECTION_PROMPT
        prompt = self._fit_and_format(ctx, prompt_template)
        if stack_correction:
            prompt = f"{stack_correction.strip()}\n\n{prompt}"
        self.agent.reset_chat()
        logger.info("Tech Architect pass 1/3 — stack selection")
        return str(self.agent.chat(prompt))

    # ------------------------------------------------------------------
    # Pass 2 — File tree (concrete filenames, ~medium)
    # ------------------------------------------------------------------

    def _pass2_file_tree(
        self,
        ctx: dict,
        stack_decisions: str,
        *,
        correction: Optional[str] = None,
    ) -> str:
        components = extract_named_components(ctx.get("solution_spec", "")) or extract_named_components(
            ctx["design_spec"]
        )
        component_section = ""
        if components:
            component_lines = "\n".join(f"- {name}" for name in components)
            component_section = (
                "NAMED COMPONENTS (each MUST have its own directory tree with "
                f"concrete source files):\n{component_lines}\n"
            )

        prompt = _PASS2_FILE_TREE_PROMPT.format(
            stack_decisions=stack_decisions,
            design_spec=ctx["design_spec"][:6000],
            skill_context=ctx["skill_context"][:3000],
            solution_contract=(ctx.get("solution_contract") or "")[:4000],
            component_section=component_section,
        )
        if correction:
            prompt = f"{correction.strip()}\n\n{prompt}"
        self.agent.reset_chat()
        logger.info("Tech Architect pass 2/3 — file tree")
        return str(self.agent.chat(prompt))

    def _validate_pass2_tree(self, ctx: dict, tree_content: str) -> list[str]:
        """Run the same structural checks the workflow uses before development."""
        from ..orchestrator.task_manager import TaskManager

        combined = tree_content
        db_path = str(self.workspace_path / "_tech_architect_validate.db") if self.workspace_path else ":memory:"
        project_id = self.workspace_path.name.replace("job-", "") if self.workspace_path else "validate"
        tm = TaskManager(db_path, project_id)
        result = tm.validate_tech_stack_completeness(
            combined,
            design_spec=ctx.get("design_spec", ""),
            solution_spec=ctx.get("solution_spec", ""),
        )
        return list(result.get("issues") or [])

    # ------------------------------------------------------------------
    # Pass 3 — Implementation plan (architecture, data flow, ~medium)
    # ------------------------------------------------------------------

    def _pass3_impl_plan(self, ctx: dict, stack_decisions: str) -> str:
        prompt = _PASS3_IMPL_PLAN_PROMPT.format(
            stack_decisions=stack_decisions,
            design_spec=ctx["design_spec"][:6000],
            vision=ctx["vision"],
        )
        self.agent.reset_chat()
        logger.info("Tech Architect pass 3/3 — implementation plan")
        return str(self.agent.chat(prompt))

    # ------------------------------------------------------------------
    # Main entry point — orchestrates all 3 passes
    # ------------------------------------------------------------------

    def define_tech_stack(
        self,
        design_spec: str,
        vision: str,
        context_digest: Optional[str] = None,
        reference_context: Optional[str] = None,
        user_stories: Optional[str] = None,
        stack_correction: Optional[str] = None,
        approved_solution: bool = False,
        solution_spec: Optional[str] = None,
    ) -> str:
        """
        Define technology stack in 3 focused LLM passes.

        Pass 1: Stack selection — technologies + justification.
        Pass 2: File tree — concrete filenames for every service.
        Pass 3: Implementation plan — architecture, data flow, security.
        """
        ctx = self._prepare_context(
            design_spec, vision, context_digest, reference_context,
            user_stories, approved_solution, solution_spec,
        )

        # Pass 1 — stack choices
        p1_response = self._pass1_stack_selection(ctx, stack_correction)

        # Pass 2 — file tree → write tech_stack.md (retry until structurally complete)
        p2_response = ""
        pass2_issues: list[str] = []
        max_pass2_attempts = 3
        for attempt in range(max_pass2_attempts):
            correction = None
            if pass2_issues:
                correction = (
                    "Your previous file tree failed structural validation:\n"
                    + "\n".join(f"- {i}" for i in pass2_issues)
                )
            p2_response = self._pass2_file_tree(ctx, p1_response, correction=correction)
            pass2_issues = self._validate_pass2_tree(ctx, p2_response)
            if not pass2_issues:
                break
            logger.warning(
                "Tech Architect pass 2 attempt %d/%d failed validation: %s",
                attempt + 1,
                max_pass2_attempts,
                pass2_issues,
            )

        if pass2_issues:
            logger.error(
                "Tech Architect pass 2 still incomplete after %d attempts: %s",
                max_pass2_attempts,
                pass2_issues,
            )

        tech_stack_body = (
            f"# Technology Stack\n\n{p1_response.strip()}\n\n## File Structure\n\n{p2_response.strip()}"
        )
        if self.workspace_path:
            tech_stack_path = self.workspace_path / "tech_stack.md"
            tech_stack_path.write_text(tech_stack_body, encoding="utf-8")

        # Pass 3 — implementation plan → write implementation_plan.md
        p3_response = self._pass3_impl_plan(ctx, p1_response)
        if self.workspace_path:
            impl_plan_path = self.workspace_path / "implementation_plan.md"
            self._extract_and_write(p3_response, impl_plan_path, "implementation_plan")

        combined = f"{p1_response}\n\n{p2_response}\n\n{p3_response}"
        return combined
    
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
        user_stories: Optional[str] = None,
        stack_correction: Optional[str] = None,
        approved_solution: bool = False,
        solution_spec: Optional[str] = None,
    ) -> str:
        """
        Run the Tech Architect agent workflow
        
        Args:
            design_spec: Design specification content
            vision: Project vision
            context_digest: Optional Project Context Digest
            reference_context: Optional RAG-retrieved reference excerpts
            user_stories: Optional user stories for capability inference
            stack_correction: Optional correction message when a prior stack over-scoped
            approved_solution: When True, solution_spec is binding (human reviewed)
            solution_spec: Approved solution specification text
        
        Returns:
            Result message
        """
        return self.define_tech_stack(
            design_spec,
            vision,
            context_digest,
            reference_context=reference_context,
            user_stories=user_stories,
            stack_correction=stack_correction,
            approved_solution=approved_solution,
            solution_spec=solution_spec,
        )


_PASS1_STACK_SELECTION_PROMPT = """\
You are the Technical Architect. This is PASS 1 of 3 — stack selection ONLY.

{stack_brief}

{solution_contract}

Design Specification (summary):
{design_spec}

Project Context:
{context_digest}

Project Vision:
{vision}

FRAMEWORK REFERENCE:
{skill_context}

━━━ TASK — Select Technologies ━━━

Based on the vision and design spec, select the technologies for each tier.
For EACH technology you select, provide a one-line justification.

Output a markdown table with columns: Layer | Technology | Justification

Do NOT list files or folder structures — that comes in a later pass.
Keep your response concise — under 1500 words.
"""

_PASS2_FILE_TREE_PROMPT = """\
You are the Technical Architect. This is PASS 2 of 3 — file tree ONLY.

The stack has been decided (below). Now enumerate every concrete file the
developer agents need to create — entry points, modules, services, models,
controllers, and tests for EACH named component.

{solution_contract}

{component_section}

STACK DECISIONS (from pass 1):
{stack_decisions}

Design Specification (for entity and module names):
{design_spec}

FRAMEWORK REFERENCE (for folder conventions):
{skill_context}

━━━ TASK — Enumerate File Tree ━━━

Output a file tree inside a markdown code fence using Unicode tree characters
(├──, └──, │). This format is REQUIRED — the orchestrator parses it to register
per-file dev tasks.

Example (note: every line is a FILE with an extension, not a folder):
```
project-root/
├── apps/
│   ├── api/
│   │   ├── src/
│   │   │   ├── main.ts
│   │   │   ├── app.module.ts
│   │   │   └── users/
│   │   │       ├── users.controller.ts
│   │   │       └── users.service.ts
│   │   ├── package.json
│   │   └── Containerfile
│   └── web/
│       ├── src/pages/index.tsx
│       └── package.json
├── docker-compose.yml
└── package.json
```

Rules:
1. EVERY line must be a concrete file with extension — NO folder-only entries.
2. Each named component above gets its own subtree with at least 3 source files
   (entry point, module/service, and one domain file).
3. Use entity and module names from the design spec in filenames.
4. Include per-service package manifests (package.json, pom.xml, etc.) and Containerfiles.
5. Do NOT collapse multiple services into one directory.

Wrap the tree in <tech_stack>...</tech_stack> tags.
No prose — tree only.
"""

_PASS3_IMPL_PLAN_PROMPT = """\
You are the Technical Architect. This is PASS 3 of 3 — implementation plan.

STACK DECISIONS (from pass 1):
{stack_decisions}

Design Specification (summary):
{design_spec}

Project Vision:
{vision}

━━━ TASK — Write Implementation Plan ━━━

Write a logical implementation plan covering:
1. Architectural overview — design patterns, service boundaries.
2. Core components — business logic, database schema, key modules.
3. Data flow — request paths through the system (text or Mermaid).
4. Integration strategy — how frontend talks to backend, inter-service communication.
5. Security & error handling — auth, validation, rate limiting.

Wrap your output in <implementation_plan>...</implementation_plan> tags.
Keep your response focused — under 2000 words.
"""

_DEFAULT_TECH_STACK_PROMPT = _PASS1_STACK_SELECTION_PROMPT


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
