"""
DevOps Agent — Creates Containerfile(s) and pipeline YAML (default: OpenShift Pipelines / Tekton).
Uses externalized standards from prompts/devops/standards/ (UBI, OCP best practices).
"""
import logging
from pathlib import Path
from typing import Optional, List

from .base_agent import BaseLlamaIndexAgent
from ..tools.file_tools import create_workspace_file_tools
from ..budget.tracker import EnhancedBudgetTracker
from ..utils.prompt_loader import load_prompt

logger = logging.getLogger(__name__)

# Fallbacks when prompt/standards files are missing
DEVOPS_BACKSTORY = (
    "You are a DevOps engineer. You create production-ready Containerfiles using "
    "Red Hat UBI base images only and OpenShift/OCP best practices (non-root user, "
    "root group permissions, no privileged ports). You create OpenShift Pipelines (Tekton) "
    "pipeline YAML by default. Use the file_writer tool to write files under the workspace."
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
            goal="Create Containerfile(s) and CI/CD pipeline (Tekton) following UBI and OCP standards.",
            backstory=backstory,
            tools=tools,
            agent_type="worker",
            budget_tracker=tracker,
            verbose=True,
        )

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
    ) -> str:
        """Build the task prompt from inputs. Externalized standards are always included."""
        sections: List[str] = []

        sections.append(f"## Tech stack\n{tech_stack}")
        sections.append(f"## Pipeline type\n{pipeline_type}")

        externalized = self._load_externalized_standards()
        sections.append(f"## Containerfile and pipeline standards (must follow)\n{externalized}")

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

        sections.append("## Instructions\nCreate and write the following using file_writer:")
        sections.append("- One or more Containerfile(s) (UBI-based, OCP-compliant).")
        sections.append(f"- {pipeline_instruction}")
        return "\n\n".join(sections)

    def run(
        self,
        tech_stack: str,
        pipeline_type: str = "tekton",
        standards_context: Optional[str] = None,
        project_context: Optional[str] = None,
    ) -> str:
        """Run the DevOps agent to produce Containerfile and pipeline files."""
        prompt = self.build_prompt(
            tech_stack=tech_stack,
            pipeline_type=pipeline_type,
            standards_context=standards_context,
            project_context=project_context,
        )
        return str(self.agent.chat(prompt))
