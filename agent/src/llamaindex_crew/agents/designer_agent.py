"""
Designer Agent - Creates high-level design specifications
Migrated from DesignerCrew to LlamaIndex agent
"""
import logging
from pathlib import Path
from typing import Optional, Union
from .base_agent import BaseLlamaIndexAgent
from ..tools import FileWriterTool, create_workspace_file_tools, prefetch_skills, append_tldr_tools
from ..tools.tool_loader import load_tools
from ..config import ConfigLoader
from ..utils.prompt_loader import load_prompt
from ..utils.llm_config import get_supports_react
from ..utils.output_parser import simple_mode_format_instruction, write_files_from_response
from ..utils.vision_stack_analysis import (
    build_stack_selection_brief,
    format_approved_solution_contract,
)


def _format_stack_manifest_section(workspace_path) -> str:
    """Binding stack_manifest constraints for Designer / Tech Architect prompts."""
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
        "BINDING STACK MANIFEST (locked early — outranks design drafts for stack breadth):\n"
        f"{json.dumps(manifest, indent=2)}\n"
        "Do NOT invent tiers listed in forbidden_tiers. "
        "chosen_stack and delivery_surface are binding constraints."
    )

logger = logging.getLogger(__name__)

_DEFAULT_DESIGN_PROMPT = """\
You are the High-Level Designer. Your goal is to translate user stories into a \
logical, implementation-ready architecture.

Match design complexity to what the vision actually requires — do not add application \
platforms, databases, or backend tiers unless the vision or user stories need them.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
INPUTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

User Stories:
{user_stories}

Project Context:
{context_digest}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FRAMEWORK REFERENCE (when required by vision — not a default platform choice)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{skill_context}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TASK — Create design_spec.md
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Assess project complexity from user stories.
2. For SIMPLE projects: describe main components/functions and UI if needed.
3. For COMPLEX projects: Bounded Contexts, Data Flow, Domain Events, C4 diagrams.
4. Define interface contracts.

Use framework terminology only when the vision requires that framework.
The goal is a design a developer can implement immediately — not over-scoped architecture.

Call file_writer(file_path='design_spec.md', content='<your design>')
"""


class DesignerAgent:
    """Designer Agent for creating logical architecture"""
    
    def __init__(
        self,
        custom_backstory: Optional[str] = None,
        budget_tracker=None,
        workspace_path: Optional[Union[str, Path]] = None,
    ):
        """
        Initialize Designer Agent
        
        Args:
            custom_backstory: Optional custom backstory (from Meta Agent)
            budget_tracker: Optional budget tracker instance
            workspace_path: When set, file tools write to this path (avoids thread-local/env issues).
        """
        self.workspace_path = Path(workspace_path) if workspace_path else None

        default_backstory = load_prompt(
            'designer/high_level_designer_backstory.txt',
            fallback="""You are a High-Level Design Agent.
Your goal is to design logical architecture without committing to specific technologies.
You use Domain-Driven Design (DDD), identify Bounded Contexts, define Data Flow and Domain Events.
You create C4 Model diagrams and define component capabilities."""
        )

        backstory = custom_backstory or default_backstory

        # Determine whether the configured model can handle ReAct tool loops.
        # Weak/free models crash in multi-turn ReAct; for them we use SimpleAgent
        # and write design_spec.md manually from the raw response.
        self.supports_react = get_supports_react("manager")
        logger.info("DesignerAgent: supports_react=%s", self.supports_react)

        tools = []
        if self.supports_react and workspace_path is not None:
            ws_tools = create_workspace_file_tools(Path(workspace_path))
            tools = [ws_tools[0]]  # file_writer only (design_spec.md is a single file)
            append_tldr_tools(tools, Path(workspace_path))

        try:
            config = ConfigLoader.load()
            entries = config.tools.global_tools + config.tools.agent_tools.get("designer", [])
            extra_tools = load_tools(entries)
            tools.extend(extra_tools)
            if extra_tools:
                backstory += (
                    "\n\nFramework-specific skills are automatically injected into your task "
                    "prompt. Use that knowledge to produce a design that references the "
                    "framework's real components, data modelling, and UI patterns."
                )
            logger.info("DesignerAgent: loaded %d extra tool(s) from config", len(extra_tools))
        except Exception:
            logger.warning("DesignerAgent: failed to load extra tools — continuing with built-ins", exc_info=True)

        self.agent = BaseLlamaIndexAgent(
            role="High-Level Designer",
            goal="Design logical architecture and system boundaries",
            backstory=backstory,
            tools=tools,
            agent_type="manager",
            budget_tracker=budget_tracker,
            verbose=True
        )
    
    def create_design_spec(
        self,
        user_stories: str,
        context_digest: Optional[str] = None,
        vision: Optional[str] = None,
        reference_context: Optional[str] = None,
        stack_correction: Optional[str] = None,
        approved_solution: bool = False,
        solution_spec: Optional[str] = None,
    ) -> str:
        """
        Create design specification based on user stories
        
        Args:
            user_stories: User stories content
            context_digest: Optional Project Context Digest
            vision: Original project vision (anchors design to user intent)
        
        Returns:
            Result message
        """
        skill_context = ""
        if vision:
            skill_context = prefetch_skills(
                vision=vision,
                role="designer",
                workspace_path=self.workspace_path,
            )
            if not (skill_context or "").strip():
                skill_context = (
                    "(No indexed skill matched closely enough.) Infer architecture from "
                    "the STACK SELECTION BRIEF and vision. Use framework terminology "
                    "only when the vision requires that framework."
                )

        stack_brief = build_stack_selection_brief(
            vision or "",
            user_stories or "",
            approved_solution=approved_solution,
        )
        manifest_section = _format_stack_manifest_section(self.workspace_path)
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

        task_prompt = load_prompt(
            'designer/create_design_spec_task.txt',
            fallback=_DEFAULT_DESIGN_PROMPT,
        )

        prompt = task_prompt.format(
            user_stories=user_stories,
            context_digest=context_digest or "",
            skill_context=skill_context,
        )

        if vision:
            prompt = (
                f"ORIGINAL PROJECT VISION (this is the ground truth — your design MUST implement this):\n"
                f"{vision}\n\n"
                f"{solution_contract + chr(10) + chr(10) if solution_contract else ''}"
                f"{stack_brief}\n\n"
                f"{manifest_section + chr(10) + chr(10) if manifest_section else ''}"
                f"{prompt}"
            )
        elif stack_brief:
            prefix = f"{solution_contract + chr(10) + chr(10) if solution_contract else ''}{stack_brief}\n\n"
            if manifest_section:
                prefix = f"{solution_contract + chr(10) + chr(10) if solution_contract else ''}{stack_brief}\n\n{manifest_section}\n\n"
            prompt = f"{prefix}{prompt}"
        elif manifest_section:
            prompt = f"{manifest_section}\n\n{prompt}"
        elif solution_contract:
            prompt = f"{solution_contract}\n\n{prompt}"
        if stack_correction:
            prompt = f"{stack_correction.strip()}\n\n{prompt}"
        if reference_context and reference_context.strip():
            prompt = (
                f"REFERENCE DOCUMENT EXCERPTS (retrieved — use for architecture decisions):\n"
                f"{reference_context.strip()}\n\n{prompt}"
            )

        if not self.supports_react:
            prompt += simple_mode_format_instruction("design_spec.md")

        response_str = str(self.agent.chat(prompt))

        from ..tools.file_tools import _resolve_workspace
        ws_path = self.workspace_path or _resolve_workspace()
        out_file = ws_path / "design_spec.md"

        if not self.supports_react:
            write_files_from_response(
                response_str,
                ws_path,
                target_file_path="design_spec.md",
                raw_fallback_path="design_spec.md",
                label="DesignerAgent",
            )
        elif not out_file.exists():
            logger.info("DesignerAgent: ReAct safety net — parsing response for design_spec.md")
            write_files_from_response(
                response_str,
                ws_path,
                target_file_path="design_spec.md",
                raw_fallback_path="design_spec.md",
                label="DesignerAgent-safetynet",
            )

        return response_str
    
    def run(self, user_stories: str, context_digest: Optional[str] = None,
            vision: Optional[str] = None, reference_context: Optional[str] = None,
            stack_correction: Optional[str] = None,
            approved_solution: bool = False,
            solution_spec: Optional[str] = None) -> str:
        """
        Run the Designer agent workflow
        
        Args:
            user_stories: User stories content
            context_digest: Optional Project Context Digest
            vision: Original project vision
            reference_context: Optional RAG-retrieved reference excerpts
            approved_solution: When True, solution_spec is binding (human reviewed)
            solution_spec: Approved solution specification text
        
        Returns:
            Result message
        """
        return self.create_design_spec(
            user_stories, context_digest, vision=vision, reference_context=reference_context,
            stack_correction=stack_correction,
            approved_solution=approved_solution,
            solution_spec=solution_spec,
        )
