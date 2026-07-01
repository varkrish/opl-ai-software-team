"""Solutioning loop — research, architect, critique before PO phase."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, List, Optional

from ..agents.solution_agents import (
    SolutionArchitectAgent,
    SolutionCritiqueAgent,
    SolutionResearchAgent,
)

logger = logging.getLogger(__name__)


@dataclass
class SolutionResult:
    approved: bool
    pass_count: int
    spec_path: Path
    candidates_path: Path
    critique_history: list = field(default_factory=list)


def _extract_json(text: str, expect_list: bool = False) -> Any:
    """Best-effort JSON extraction from agent output."""
    text = (text or "").strip()
    if not text:
        return [] if expect_list else {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    pattern = r"\[[\s\S]*\]" if expect_list else r"\{[\s\S]*\}"
    match = re.search(pattern, text)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return [] if expect_list else {}


def _format_critique_feedback(critique: dict) -> str:
    parts = []
    must_fix = critique.get("must_fix") or []
    issues = critique.get("issues") or []
    if must_fix:
        parts.append("Must fix:\n- " + "\n- ".join(str(x) for x in must_fix))
    if issues:
        parts.append("Issues:\n- " + "\n- ".join(str(x) for x in issues))
    return "\n\n".join(parts)


def run_solutioning_loop(
    vision: str,
    project_context: str,
    workspace_path: Path,
    config: "SecretConfig",
    budget_tracker,
    document_indexer,
    max_passes: int = 3,
    progress_callback: Optional[Callable[[str, int, str], None]] = None,
    max_github_searches: int = 10,
    github_token: Optional[str] = None,
) -> SolutionResult:
    """Run the research → architect → critique loop (hard-capped at max_passes)."""
    workspace_path = Path(workspace_path)
    workspace_path.mkdir(parents=True, exist_ok=True)

    candidates_path = workspace_path / "solution_candidates.json"
    spec_path = workspace_path / "solution_spec.md"
    critique_history: List[dict] = []

    def _progress(step: str, pct: int, msg: str) -> None:
        if progress_callback:
            try:
                progress_callback(step, pct, msg)
            except Exception:
                logger.warning("Solutioning progress callback failed", exc_info=True)

    research_agent = SolutionResearchAgent(
        budget_tracker=budget_tracker,
        document_indexer=document_indexer,
        workspace_path=workspace_path,
        config=config,
        max_github_searches=max_github_searches,
        github_token=github_token,
    )
    architect_agent = SolutionArchitectAgent(
        budget_tracker=budget_tracker,
        workspace_path=workspace_path,
        config=config,
    )
    critique_agent = SolutionCritiqueAgent(
        budget_tracker=budget_tracker,
        config=config,
    )

    _progress("solutioning", 12, "Researching solution candidates…")
    research_raw = research_agent.run(vision, project_context)
    candidates = _extract_json(research_raw, expect_list=True)
    if not isinstance(candidates, list):
        candidates = [candidates] if candidates else []
    candidates_path.write_text(json.dumps(candidates, indent=2) + "\n", encoding="utf-8")
    candidates_json = candidates_path.read_text(encoding="utf-8")

    feedback = ""
    approved = False
    pass_count = 0

    while pass_count < max_passes:
        pass_count += 1
        _progress("solutioning", 15 + pass_count * 5, f"Architect pass {pass_count}…")
        architect_agent.run(vision, project_context, candidates_json, feedback=feedback)

        if not spec_path.exists():
            spec_path.write_text(
                f"# Solution Specification\n\nDraft for: {vision[:200]}\n",
                encoding="utf-8",
            )
        spec_content = spec_path.read_text(encoding="utf-8", errors="replace")

        # Archive this pass's spec so revisions can be diffed pass-over-pass —
        # spec_path itself gets overwritten on the next pass.
        spec_pass_file = workspace_path / f"solution_spec_pass_{pass_count}.md"
        spec_pass_file.write_text(spec_content, encoding="utf-8")

        _progress("solutioning", 18 + pass_count * 5, f"Critique pass {pass_count}…")
        critique_raw = critique_agent.run(vision, spec_content, candidates_json, project_context)
        critique = _extract_json(critique_raw, expect_list=False)
        if not isinstance(critique, dict):
            critique = {"approved": False, "score": 0, "issues": ["Invalid critique JSON"], "must_fix": []}

        critique_file = workspace_path / f"solution_critique_pass_{pass_count}.json"
        critique_file.write_text(json.dumps(critique, indent=2) + "\n", encoding="utf-8")
        critique_history.append(critique)

        if critique.get("approved"):
            approved = True
            break

        if pass_count >= max_passes:
            approved = False
            break

        feedback = _format_critique_feedback(critique)

    return SolutionResult(
        approved=approved,
        pass_count=pass_count,
        spec_path=spec_path,
        candidates_path=candidates_path,
        critique_history=critique_history,
    )
