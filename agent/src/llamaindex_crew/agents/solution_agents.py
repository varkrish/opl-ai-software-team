"""Solutioning loop agents — research, architect, critique."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional, Union

from ..tools import create_workspace_file_tools
from ..tools.github_search_tools import GitHubRepoReadmeTool, GitHubSearchReposTool
from ..tools.skill_tools import SkillQueryTool
from ..tools.tldr_tools import _TLDR_AGENT_BACKSTORY, append_tldr_tools
from ..tools.tool_loader import load_tools
from ..utils.llm_config import get_supports_react
from ..utils.prompt_loader import load_prompt
from ..utils.output_parser import (
    clean_llm_response_text,
    is_llm_stub_content,
    is_valid_markdown_artifact,
    looks_like_raw_agent_dump,
)
from .base_agent import BaseLlamaIndexAgent

logger = logging.getLogger(__name__)


class SolutionResearchAgent:
    """Research agent — discovers solution candidates via GitHub + skills."""

    def __init__(
        self,
        budget_tracker=None,
        document_indexer=None,
        workspace_path: Optional[Union[str, Path]] = None,
        config=None,
        max_github_searches: int = 10,
        github_token: Optional[str] = None,
    ):
        self.config = config
        self.workspace_path = Path(workspace_path) if workspace_path else None
        token = github_token or os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
        tools = [
            GitHubSearchReposTool(token, max_calls=max_github_searches),
            GitHubRepoReadmeTool(token),
        ]
        skills_url = None
        if config and getattr(config, "skills", None):
            skills_url = getattr(config.skills, "service_url", None)
        if skills_url:
            tools.append(SkillQueryTool(service_url=skills_url))
        if self.workspace_path is not None and get_supports_react("manager"):
            append_tldr_tools(tools, self.workspace_path, config=config)

        try:
            if config:
                entries = config.tools.global_tools + config.tools.agent_tools.get("solution_research", [])
                tools.extend(load_tools(entries))
        except Exception:
            logger.warning("SolutionResearchAgent: failed to load extra tools", exc_info=True)

        backstory = (
            "You research open-source reference implementations and framework patterns "
            "to propose solution candidates before detailed planning."
        )
        if any(getattr(t, "metadata", None) and t.metadata.name in {
            "code_search", "code_structure", "code_context", "code_impact",
        } for t in tools):
            backstory += _TLDR_AGENT_BACKSTORY
        self.agent = BaseLlamaIndexAgent(
            role="Solution Researcher",
            goal="Find reference repos and patterns for the project vision",
            backstory=backstory,
            tools=tools,
            agent_type="manager",
            budget_tracker=budget_tracker,
        )

    def run(self, vision: str, project_context: str) -> str:
        prompt = load_prompt(
            "solutioning/research_task.txt",
            fallback="Return JSON list of solution candidates for: {vision}",
        ).format(vision=vision, project_context=project_context or "")
        return str(self.agent.chat(prompt))


class SolutionArchitectAgent:
    """Architect agent — writes solution_spec.md from candidates."""

    def __init__(
        self,
        budget_tracker=None,
        workspace_path: Optional[Union[str, Path]] = None,
        config=None,
    ):
        self.workspace_path = Path(workspace_path) if workspace_path else None
        self.config = config
        tools = []
        if workspace_path is not None and get_supports_react("manager"):
            ws_tools = create_workspace_file_tools(Path(workspace_path))
            tools = [ws_tools[0], ws_tools[1]]
            append_tldr_tools(tools, Path(workspace_path), config=config)

        backstory = (
            "You synthesize research into a concise solution specification "
            "that downstream product and engineering agents can follow."
        )
        if any(getattr(t, "metadata", None) and t.metadata.name in {
            "code_search", "code_structure", "code_context", "code_impact",
        } for t in tools):
            backstory += _TLDR_AGENT_BACKSTORY
        self.agent = BaseLlamaIndexAgent(
            role="Solution Architect",
            goal="Write solution_spec.md from research candidates",
            backstory=backstory,
            tools=tools,
            agent_type="manager",
            budget_tracker=budget_tracker,
        )

    def run(
        self,
        vision: str,
        project_context: str,
        candidates_json: str,
        feedback: str = "",
    ) -> str:
        feedback_section = ""
        if feedback.strip():
            feedback_section = f"Critique feedback to address:\n{feedback.strip()}\n"
        prompt = load_prompt(
            "solutioning/architect_task.txt",
            fallback="Write solution_spec.md for vision: {vision}",
        ).format(
            vision=vision,
            project_context=project_context or "",
            candidates_json=candidates_json,
            feedback_section=feedback_section,
        )
        spec_path = self.workspace_path / "solution_spec.md" if self.workspace_path else None
        content_before = (
            spec_path.read_text(encoding="utf-8", errors="replace")
            if spec_path and spec_path.exists()
            else None
        )

        # Each architect pass is a fresh task; stale tool messages from pass N-1
        # break Vertex/MaaS ("tool role without previous assistant message").
        self.agent.reset_chat()
        result = str(self.agent.chat(prompt))

        if spec_path and result.strip():
            content_after = (
                spec_path.read_text(encoding="utf-8", errors="replace")
                if spec_path.exists()
                else None
            )
            if content_after == content_before:
                # The agent didn't write solution_spec.md via its file tool this
                # pass (no tools available, or it just replied in chat) — persist
                # cleaned markdown when it looks like a real spec, not channel stubs.
                candidate = clean_llm_response_text(result)
                if (
                    not is_llm_stub_content(candidate)
                    and not looks_like_raw_agent_dump(result)
                    and is_valid_markdown_artifact(candidate, min_chars=80, min_lines=3)
                ):
                    spec_path.write_text(candidate.strip() + "\n", encoding="utf-8")
                elif get_supports_react("manager") and hasattr(self.agent, "chat_simple"):
                    logger.warning(
                        "Solution architect ReAct stub (%d chars) — trying chat_simple fallback",
                        len(result),
                    )
                    self.agent.reset_chat()
                    simple_result = str(self.agent.chat_simple(
                        prompt
                        + "\n\nOutput ONLY the complete solution_spec.md markdown "
                        "(start with `# Solution Specification`). "
                        "No tools, no commentary, no channel tokens."
                    ))
                    simple_candidate = clean_llm_response_text(simple_result)
                    if (
                        not is_llm_stub_content(simple_candidate)
                        and not looks_like_raw_agent_dump(simple_result)
                        and is_valid_markdown_artifact(simple_candidate, min_chars=80, min_lines=3)
                    ):
                        spec_path.write_text(simple_candidate.strip() + "\n", encoding="utf-8")
                    else:
                        logger.warning(
                            "Solution architect chat_simple fallback also unparsed (%d chars) "
                            "— skipping fallback write",
                            len(simple_result),
                        )
                else:
                    logger.warning(
                        "Solution architect pass did not write solution_spec.md via tool; "
                        "response is stub/unparsed output (%d chars) — skipping fallback write",
                        len(result),
                    )
        return result


class SolutionCritiqueAgent:
    """Critique agent — approves or rejects the solution spec."""

    def __init__(self, budget_tracker=None, config=None):
        self.config = config
        backstory = (
            "You review solution specifications for feasibility, alignment, and completeness. "
            "You return structured JSON verdicts — never rewrite the spec."
        )
        self.agent = BaseLlamaIndexAgent(
            role="Solution Critique Reviewer",
            goal="Critique the solution spec and return JSON verdict",
            backstory=backstory,
            tools=[],
            agent_type="reviewer",
            budget_tracker=budget_tracker,
        )

    def run(self, vision: str, spec_content: str, candidates_json: str, project_context: str = "") -> str:
        prompt = load_prompt(
            "solutioning/critique_task.txt",
            fallback="Critique the spec and return JSON with approved/score/issues/must_fix.",
        ).format(
            vision=vision,
            candidates_json=candidates_json,
            spec_content=spec_content,
            project_context=project_context or "",
        )
        self.agent.reset_chat()
        return str(self.agent.chat(prompt))
