"""
DevOps Agent — Creates Containerfile(s) and pipeline YAML (default: OpenShift Pipelines / Tekton).

Pre-fetches framework-specific containerization skills from the skills service
so that Frappe apps get S2I/SNE-based Containerfiles (not generic UBI builds).
"""
import logging
import os
from pathlib import Path
from typing import Optional, List

import httpx

from .base_agent import BaseLlamaIndexAgent
from ..tools.file_tools import create_workspace_file_tools
from ..budget.tracker import EnhancedBudgetTracker
from ..utils.prompt_loader import load_prompt

logger = logging.getLogger(__name__)

DEVOPS_BACKSTORY = (
    "You are a DevOps engineer specialising in containerising applications. "
    "You MUST check the FRAMEWORK-SPECIFIC CONTAINERIZATION REFERENCE section in the "
    "task prompt before writing any Containerfile — different frameworks (Frappe, Django, "
    "Spring, etc.) have their own builder images and patterns. "
    "You create OpenShift Pipelines (Tekton) pipeline YAML by default. "
    "Use the file_writer tool to write files under the workspace. "
    "Also generate a compose.yml for local development if framework skills provide one."
)

MINIMAL_OCP_FALLBACK = (
    "Containerfile standards: Use Red Hat UBI base images only (e.g. ubi9/ubi-minimal). "
    "OCP best practices: run as non-root, use root group permissions where required, "
    "no privileged ports (use ports > 1024, e.g. 8080), no privileged containers."
)

MINIMAL_TEKTON_FALLBACK = (
    "Pipeline: OpenShift Pipelines (Tekton). Write Pipeline YAML to .tekton/pipeline.yaml "
    "(or equivalent). Use standard Tekton Pipeline/PipelineRun or Task resources."
)


class DevOpsAgent:
    """Produces Containerfile(s) and pipeline YAML (Tekton by default) for a project."""

    def __init__(
        self,
        workspace_path: Path,
        project_id: str,
        custom_backstory: Optional[str] = None,
        budget_tracker: Optional[EnhancedBudgetTracker] = None,
    ):
        self.workspace_path = Path(workspace_path)
        self.project_id = project_id
        tools = create_workspace_file_tools(self.workspace_path)
        tracker = budget_tracker or EnhancedBudgetTracker()
        tracker.project_id = project_id

        backstory = load_prompt(
            "devops/devops_backstory.txt",
            fallback=DEVOPS_BACKSTORY,
        )
        if custom_backstory:
            backstory = custom_backstory

        self.agent = BaseLlamaIndexAgent(
            role="DevOps Agent",
            goal="Create Containerfile(s) and CI/CD pipeline (Tekton) following framework-specific best practices.",
            backstory=backstory,
            tools=tools,
            agent_type="worker",
            budget_tracker=tracker,
            verbose=True,
        )

    # ------------------------------------------------------------------
    # Skills pre-fetch — mirrors TechArchitectAgent._prefetch_skills
    # ------------------------------------------------------------------

    @staticmethod
    def _prefetch_containerization_skills(tech_stack: str) -> str:
        """Query the skills service for framework-specific containerization patterns."""
        try:
            service_url = os.environ.get("SKILLS_SERVICE_URL", "").rstrip("/")
            if not service_url:
                try:
                    from ..config import ConfigLoader
                    config = ConfigLoader.load()
                    url_obj = getattr(config, "skills", None)
                    service_url = getattr(url_obj, "service_url", "") if url_obj else ""
                except Exception:
                    pass
            if not service_url:
                return ""

            queries = [
                f"{tech_stack} containerfile container image builder",
                f"{tech_stack} compose development local container",
            ]
            sections: list[str] = []
            seen: set[str] = set()
            for q in queries:
                resp = httpx.post(
                    f"{service_url}/query",
                    json={"query": q, "top_k": 3},
                    timeout=15,
                )
                resp.raise_for_status()
                for r in resp.json().get("results", []):
                    key = (r["skill_name"], r["content"][:80])
                    if key not in seen:
                        seen.add(key)
                        sections.append(f"[Skill: {r['skill_name']}]\n{r['content']}")

            if sections:
                logger.info(
                    "DevOpsAgent: pre-fetched %d containerization skill sections",
                    len(sections),
                )
                return "\n\n---\n\n".join(sections)
        except Exception:
            logger.warning("DevOpsAgent: skill pre-fetch failed", exc_info=True)
        return ""

    # ------------------------------------------------------------------

    def _load_externalized_standards(self) -> str:
        """Load OCP/UBI and Tekton standards from prompts/devops/standards/."""
        parts: List[str] = []
        ocp = load_prompt(
            "devops/standards/ocp_containerfile_standards.txt",
            fallback=MINIMAL_OCP_FALLBACK,
        )
        parts.append(ocp)
        tekton = load_prompt(
            "devops/standards/tekton_defaults.txt",
            fallback=MINIMAL_TEKTON_FALLBACK,
        )
        parts.append(tekton)
        return "\n\n".join(parts)

    def build_prompt(
        self,
        tech_stack: str,
        pipeline_type: str = "tekton",
        standards_context: Optional[str] = None,
        project_context: Optional[str] = None,
        skill_context: Optional[str] = None,
    ) -> str:
        """Build the task prompt. Framework skills take priority over generic standards."""
        sections: List[str] = []

        sections.append(f"## Tech stack\n{tech_stack}")
        sections.append(f"## Pipeline type\n{pipeline_type}")

        if skill_context:
            sections.append(
                "## FRAMEWORK-SPECIFIC CONTAINERIZATION REFERENCE (GROUND TRUTH — follow this)\n"
                "The following skills describe the CORRECT way to containerise this application.\n"
                "You MUST follow these patterns instead of inventing your own Containerfile.\n"
                "If the skill recommends S2I builder images, use those — NOT generic UBI images.\n"
                "If the skill provides a compose.yml template, generate one as well.\n\n"
                f"{skill_context}"
            )

        externalized = self._load_externalized_standards()
        sections.append(
            "## Generic Containerfile and pipeline standards (use ONLY if no framework skill above)\n"
            f"{externalized}"
        )

        if standards_context:
            sections.append(f"## Additional standards (from request)\n{standards_context}")
        if project_context:
            sections.append(f"## Project context\n{project_context}")

        if pipeline_type == "tekton":
            pipeline_instruction = (
                "Write at least one Containerfile and a Tekton Pipeline (e.g. .tekton/pipeline.yaml). "
                "Use file_writer for each file."
            )
        elif pipeline_type == "github_actions":
            pipeline_instruction = (
                "Write at least one Containerfile and a GitHub Actions workflow (e.g. .github/workflows/ci.yml). "
                "Use file_writer for each file."
            )
        else:
            pipeline_instruction = (
                f"Write at least one Containerfile and pipeline configuration for {pipeline_type}. "
                "Use file_writer for each file."
            )

        sections.append(
            "## Instructions\nCreate and write the following using file_writer:\n"
            "- Containerfile(s) matching the framework skill above (e.g. S2I-based for Frappe).\n"
            "- apps.json if the skill requires it.\n"
            "- compose.yml for local development if the skill provides a template.\n"
            f"- {pipeline_instruction}"
        )
        return "\n\n".join(sections)

    def run(
        self,
        tech_stack: str,
        pipeline_type: str = "tekton",
        standards_context: Optional[str] = None,
        project_context: Optional[str] = None,
    ) -> str:
        """Run the DevOps agent to produce Containerfile and pipeline files."""
        skill_context = self._prefetch_containerization_skills(tech_stack)

        prompt = self.build_prompt(
            tech_stack=tech_stack,
            pipeline_type=pipeline_type,
            standards_context=standards_context,
            project_context=project_context,
            skill_context=skill_context,
        )
        return str(self.agent.chat(prompt))
