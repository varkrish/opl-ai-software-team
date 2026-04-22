"""
Designer Agent - Creates high-level design specifications
Migrated from DesignerCrew to LlamaIndex agent
"""
import logging
from pathlib import Path
from typing import Optional, Union
from .base_agent import BaseLlamaIndexAgent
from ..tools import FileWriterTool, create_workspace_file_tools, prefetch_skills
from ..tools.tool_loader import load_tools
from ..config import ConfigLoader
from ..utils.prompt_loader import load_prompt

logger = logging.getLogger(__name__)

_DEFAULT_DESIGN_PROMPT = """\
You are the High-Level Designer. Your goal is to translate user stories into a \
logical, implementation-ready architecture.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
INPUTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

User Stories:
{user_stories}

Project Context:
{context_digest}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FRAMEWORK REFERENCE (from skills — use this to inform your design)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
The following skill documents describe real conventions for the target framework.
Use them to make your design CONCRETE and framework-aware.

{skill_context}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TASK — Create design_spec.md
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Assess project complexity from user stories.
2. For SIMPLE projects: describe main components/functions and UI if needed.
3. For COMPLEX projects: Bounded Contexts, Data Flow, Domain Events, C4 diagrams.
4. Define interface contracts.

CRITICAL — Use the FRAMEWORK REFERENCE above to:
- Name components and modules using the framework's actual terminology \
(e.g. DocType, Page, Report for Frappe; Component, Service, Hook for React).
- Describe domain entities using the framework's data modelling approach \
(e.g. DocType JSON + controller.py for Frappe, Prisma schema for Node).
- Specify UI patterns the framework actually supports \
(e.g. Frappe Page, Workspace, Dialog; React Router, Context).
- List framework-specific integration points \
(e.g. hooks.py, fixtures, whitelisted APIs for Frappe).
- When showing file paths, use the EXACT nesting from the FRAMEWORK REFERENCE.

FRAPPE FILE PATHS — MANDATORY RULE:
If this is a Frappe app, every file path MUST use THREE levels of nesting:
  app_name/app_name/module_name/doctype/doctype_name/file
Example for app "leave_tracker", module "Leave Tracker":
  CORRECT: leave_tracker/leave_tracker/leave_tracker/doctype/leave_request/leave_request.json
  WRONG:   leave_tracker/leave_tracker/doctype/leave_request/leave_request.json
The doctype/ folder is ALWAYS inside a module folder, never directly under the inner package.

The goal is a design that a developer familiar with the framework can \
immediately start implementing — not a generic DDD document.

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

        if workspace_path is not None:
            ws_tools = create_workspace_file_tools(self.workspace_path)
            tools = [ws_tools[0]]  # file_writer
        else:
            tools = [FileWriterTool]

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
                f"{vision}\n\n{prompt}"
            )
        
        response = self.agent.chat(prompt)
        
        return str(response)
    
    def run(self, user_stories: str, context_digest: Optional[str] = None,
            vision: Optional[str] = None) -> str:
        """
        Run the Designer agent workflow
        
        Args:
            user_stories: User stories content
            context_digest: Optional Project Context Digest
            vision: Original project vision
        
        Returns:
            Result message
        """
        return self.create_design_spec(user_stories, context_digest, vision=vision)
