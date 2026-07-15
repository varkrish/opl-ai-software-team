"""Solutioning loop — research, architect, critique before PO phase."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from ..agents.solution_agents import (
    SolutionArchitectAgent,
    SolutionCritiqueAgent,
    SolutionResearchAgent,
)
from ..utils.vision_stack_analysis import CapabilityProfile, infer_capability_profile

logger = logging.getLogger(__name__)

STACK_MANIFEST_FILENAME = "stack_manifest.json"
_REQUIRED_MANIFEST_KEYS = (
    "path",
    "delivery_surface",
    "complexity",
    "chosen_stack",
    "forbidden_tiers",
    "rationale",
    "skills_query",
)

_CLIENT_FORBIDDEN_TIERS = ["application_server", "database", "cms_platform"]


@dataclass
class SolutionResult:
    approved: bool
    pass_count: int
    spec_path: Path
    candidates_path: Path
    critique_history: list = field(default_factory=list)
    path: str = "full"
    stack_manifest_path: Optional[Path] = None


def write_stack_manifest(workspace_path: Path, data: Dict[str, Any]) -> Path:
    """Persist stack_manifest.json with required contract keys."""
    workspace_path = Path(workspace_path)
    workspace_path.mkdir(parents=True, exist_ok=True)
    payload = dict(data)
    for key in _REQUIRED_MANIFEST_KEYS:
        payload.setdefault(key, [] if key in ("chosen_stack", "forbidden_tiers") else "")
    path = workspace_path / STACK_MANIFEST_FILENAME
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def read_stack_manifest(workspace_path: Path) -> Optional[Dict[str, Any]]:
    """Load stack_manifest.json from workspace, or None if missing/invalid."""
    path = Path(workspace_path) / STACK_MANIFEST_FILENAME
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _minimal_chosen_stack(profile: CapabilityProfile, vision: str) -> List[str]:
    if profile.explicit_technologies:
        return list(profile.explicit_technologies)
    text = (vision or "").lower()
    from ..utils.vision_stack_analysis import _extract_named_technologies

    named = _extract_named_technologies(text)
    if named:
        return list(named)

    stack: List[str] = []
    # Backend / API visions must not fall through to html/css.
    is_backend = (
        profile.needs_api
        or profile.needs_server_runtime
        or profile.delivery_surface in ("api_service", "fullstack")
    )
    if is_backend and profile.delivery_surface != "client_deliverable":
        if re.search(r"\bgo(?:lang)?\b", text):
            return ["go"]
        if "rust" in text or "cargo" in text:
            return ["rust"]
        if "python" in text or "fastapi" in text or "django" in text or "flask" in text:
            return ["python"]
        if "java" in text or "spring" in text:
            return ["java"]
        if "node" in text or "typescript" in text or "express" in text:
            return ["typescript"] if "typescript" in text else ["javascript"]
        # Unknown backend language — prefer go for api_service, else python
        return ["go"] if profile.delivery_surface == "api_service" else ["python"]

    if "html" in text or "<!doctype" in text or "<html" in text:
        stack.append("html")
    if "css" in text or "colour" in text or "color" in text or "style" in text:
        stack.append("css")
    if "svg" in text:
        stack.append("svg")
    if "javascript" in text or re.search(r"\bes\s+module", text):
        stack.append("javascript")
    if not stack:
        # Client deliverable with no cues — html/css is appropriate
        stack = ["html", "css"]
    seen = set()
    ordered: List[str] = []
    for item in stack:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def _skills_query_for_profile(profile: CapabilityProfile, chosen_stack: Sequence[str]) -> str:
    if profile.explicit_technologies:
        return " ".join(profile.explicit_technologies)
    parts = list(chosen_stack) + ["accessibility"]
    if profile.delivery_surface == "client_deliverable":
        parts.insert(0, "vanilla")
    return " ".join(parts)


def _minimal_solution_spec(
    vision: str,
    profile: CapabilityProfile,
    chosen_stack: Sequence[str],
    forbidden_tiers: Sequence[str],
) -> str:
    stack_line = ", ".join(chosen_stack) if chosen_stack else "minimal client stack"
    forbidden = ", ".join(forbidden_tiers) if forbidden_tiers else "none"
    return (
        "# Solution Specification\n\n"
        "**Path:** fast (stack lock without Research → Critique)\n\n"
        f"**Vision:** {vision.strip()}\n\n"
        f"**Delivery surface:** {profile.delivery_surface}\n\n"
        f"**Complexity:** {profile.complexity}\n\n"
        f"**Chosen stack:** {stack_line}\n\n"
        f"**Forbidden tiers:** {forbidden}\n\n"
        "## Approach\n\n"
        "Lock the stack constraints above and proceed to Product Owner → Designer → "
        "Tech Architect. Tech Architect must still produce `tech_stack.md` and "
        "`implementation_plan.md` consistent with this contract.\n"
    )


def run_fast_stack_decision(
    vision: str,
    profile: Optional[CapabilityProfile],
    workspace_path: Path,
) -> SolutionResult:
    """
    Fast path: write stack_manifest.json + solution_spec.md without Research/Critique.
    """
    workspace_path = Path(workspace_path)
    workspace_path.mkdir(parents=True, exist_ok=True)
    resolved = profile or infer_capability_profile(vision)
    chosen = _minimal_chosen_stack(resolved, vision)
    forbidden = list(_CLIENT_FORBIDDEN_TIERS) if not resolved.needs_server_runtime else []
    rationale = (
        f"Fast path for {resolved.delivery_surface}/{resolved.complexity} vision "
        "without Research → Critique."
    )
    manifest = {
        "path": "fast",
        "delivery_surface": resolved.delivery_surface,
        "complexity": resolved.complexity,
        "chosen_stack": chosen,
        "forbidden_tiers": forbidden,
        "rationale": rationale,
        "skills_query": _skills_query_for_profile(resolved, chosen),
        "source": "fast",
        "suggested_path": resolved.suggested_path,
        "explicit_technologies": list(resolved.explicit_technologies),
        "needs_persistence": resolved.needs_persistence,
        "needs_api": resolved.needs_api,
        "needs_auth": resolved.needs_auth,
        "needs_server_runtime": resolved.needs_server_runtime,
    }
    manifest_path = write_stack_manifest(workspace_path, manifest)

    spec_path = workspace_path / "solution_spec.md"
    spec_path.write_text(
        _minimal_solution_spec(vision, resolved, chosen, forbidden),
        encoding="utf-8",
    )
    candidates_path = workspace_path / "solution_candidates.json"
    if not candidates_path.exists():
        candidates_path.write_text("[]\n", encoding="utf-8")

    return SolutionResult(
        approved=True,
        pass_count=0,
        spec_path=spec_path,
        candidates_path=candidates_path,
        critique_history=[],
        path="fast",
        stack_manifest_path=manifest_path,
    )


_SPEC_DATA_TIER_SIGNALS = re.compile(
    r"\b(?:redis|upstash|memcached|dynamodb|firestore|supabase|planetscale|"
    r"neon|turso|postgres(?:ql)?|mysql|mariadb|mongodb|sqlite|cockroachdb|"
    r"prisma|sequelize|typeorm|drizzle|knex|sqlalchemy)\b",
    re.IGNORECASE,
)

_SPEC_GENERIC_DATA_WORDS = re.compile(
    r"\b(?:cache|caching|database|orm|persistence)\b",
    re.IGNORECASE,
)

_SPEC_NEGATION_PREFIX = re.compile(
    r"(?:no|not|without|avoid|never|lack|exclude|skip)\s+"
    r"(?:a\s+|any\s+|the\s+|separate\s+|additional\s+|complex\s+)*",
    re.IGNORECASE,
)


def _spec_needs_data_tier(spec_text: str) -> bool:
    """True when the approved spec actively selects persistence, caching, or databases.

    Named products (Redis, PostgreSQL, etc.) are strong signals.
    Generic words (cache, database, orm) are only counted when not negated.
    """
    if _SPEC_DATA_TIER_SIGNALS.search(spec_text or ""):
        return True
    for m in _SPEC_GENERIC_DATA_WORDS.finditer(spec_text or ""):
        window = (spec_text or "")[max(0, m.start() - 60):m.start()]
        if not _SPEC_NEGATION_PREFIX.search(window):
            return True
    return False


def write_stack_manifest_from_solution_spec(
    vision: str,
    profile: Optional[CapabilityProfile],
    workspace_path: Path,
    spec_text: Optional[str] = None,
) -> Dict[str, Any]:
    """Derive and write a full-path stack_manifest from an approved solution_spec.

    The approved spec is the higher-authority contract — if it mentions Redis,
    caching, Postgres, etc. then ``database`` must NOT be in ``forbidden_tiers``
    even when the short vision text didn't mention persistence.
    """
    workspace_path = Path(workspace_path)
    resolved = profile or infer_capability_profile(vision)
    if spec_text is None:
        spec_file = workspace_path / "solution_spec.md"
        spec_text = (
            spec_file.read_text(encoding="utf-8", errors="replace")
            if spec_file.exists()
            else ""
        )

    chosen = list(resolved.explicit_technologies)
    lower_spec = (spec_text or "").lower()
    from ..utils.vision_stack_analysis import (
        _effective_forbidden_tiers,
        _extract_named_technologies,
        _chosen_unlocks_tier,
        _tier_markers,
    )

    for name in _extract_named_technologies(lower_spec):
        if name not in chosen:
            chosen.append(name)
    if not chosen:
        chosen = _minimal_chosen_stack(resolved, vision)

    # --- Reconcile vision profile with approved spec content ---
    spec_has_data = _spec_needs_data_tier(spec_text or "")
    needs_persistence = resolved.needs_persistence or spec_has_data

    # Start from capability-based defaults, then unlock any tier already
    # selected by chosen_stack (technology-agnostic overlap — no framework lists).
    candidate_forbidden: List[str] = []
    if resolved.delivery_surface == "client_deliverable" and not resolved.needs_server_runtime:
        candidate_forbidden = list(_CLIENT_FORBIDDEN_TIERS)

    # Approved spec explicitly uses data/cache → never forbid the database tier
    if spec_has_data and "database" in candidate_forbidden:
        candidate_forbidden.remove("database")
        logger.info(
            "Spec mentions data/cache tier — unlocking 'database' from forbidden_tiers"
        )

    forbidden = _effective_forbidden_tiers(candidate_forbidden, chosen)

    delivery_surface = resolved.delivery_surface
    needs_server = resolved.needs_server_runtime or _chosen_unlocks_tier(
        chosen, _tier_markers("application_server")
    )
    if needs_server and delivery_surface == "client_deliverable":
        delivery_surface = "fullstack"

    manifest = {
        "path": "full",
        "delivery_surface": delivery_surface,
        "complexity": resolved.complexity,
        "chosen_stack": chosen,
        "forbidden_tiers": forbidden,
        "rationale": "Derived from approved solution_spec after full solutioning loop.",
        "skills_query": _skills_query_for_profile(resolved, chosen),
        "source": "full",
        "suggested_path": resolved.suggested_path,
        "explicit_technologies": list(resolved.explicit_technologies),
        "needs_persistence": needs_persistence,
        "needs_api": resolved.needs_api or needs_server,
        "needs_auth": resolved.needs_auth,
        "needs_server_runtime": needs_server,
    }
    write_stack_manifest(workspace_path, manifest)
    return manifest


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


def _non_empty_must_fix(critique: dict) -> List[str]:
    """Return trimmed must_fix entries; empty strings are ignored."""
    raw = critique.get("must_fix") or []
    if not isinstance(raw, list):
        text = str(raw).strip()
        return [text] if text else []
    return [str(x).strip() for x in raw if str(x).strip()]


def is_critique_approved(critique: dict) -> bool:
    """True only when critique approves and there are no blocking must_fix items."""
    if not critique.get("approved"):
        return False
    return not _non_empty_must_fix(critique)


def normalize_critique(critique: dict) -> dict:
    """Force approved=false when must_fix is non-empty (LLM may contradict itself)."""
    out = dict(critique)
    if _non_empty_must_fix(out) and out.get("approved"):
        out["approved"] = False
    return out


def _format_critique_feedback(critique: dict) -> str:
    parts = []
    must_fix = _non_empty_must_fix(critique)
    issues = critique.get("issues") or []
    if must_fix:
        parts.append("Must fix:\n- " + "\n- ".join(must_fix))
    if issues:
        parts.append("Issues:\n- " + "\n- ".join(str(x) for x in issues))
    return "\n\n".join(parts)


def run_architect_critique_passes(
    vision: str,
    project_context: str,
    workspace_path: Path,
    architect_agent,
    critique_agent,
    candidates_json: str,
    *,
    max_passes: int,
    initial_feedback: str = "",
    existing_passes: int = 0,
    progress_callback: Optional[Callable[[str, int, str], None]] = None,
) -> tuple[bool, int, List[dict]]:
    """Run architect → critique up to *max_passes* times.

    *existing_passes* is the number of critique files already on disk (refine
    continues pass numbering). Returns ``(approved, total_pass_count, critiques)``.
    """
    workspace_path = Path(workspace_path)
    spec_path = workspace_path / "solution_spec.md"
    max_passes = max(1, int(max_passes or 1))
    feedback = initial_feedback
    approved = False
    passes_run = 0
    critique_history: List[dict] = []

    def _progress(step: str, pct: int, msg: str) -> None:
        if progress_callback:
            try:
                progress_callback(step, pct, msg)
            except Exception:
                logger.warning("Solutioning progress callback failed", exc_info=True)

    while passes_run < max_passes:
        pass_num = existing_passes + passes_run + 1
        passes_run += 1
        _progress(
            "solutioning",
            15 + pass_num * 5,
            f"Architect pass {pass_num} (iteration {passes_run}/{max_passes})…",
        )
        architect_agent.run(vision, project_context, candidates_json, feedback=feedback)

        if not spec_path.exists():
            spec_path.write_text(
                f"# Solution Specification\n\nDraft for: {vision[:200]}\n",
                encoding="utf-8",
            )
        spec_content = spec_path.read_text(encoding="utf-8", errors="replace")

        spec_pass_file = workspace_path / f"solution_spec_pass_{pass_num}.md"
        spec_pass_file.write_text(spec_content, encoding="utf-8")

        _progress(
            "solutioning",
            18 + pass_num * 5,
            f"Critique pass {pass_num} (iteration {passes_run}/{max_passes})…",
        )
        critique_raw = critique_agent.run(vision, spec_content, candidates_json, project_context)
        critique = _extract_json(critique_raw, expect_list=False)
        if not isinstance(critique, dict):
            critique = {
                "approved": False,
                "score": 0,
                "issues": ["Invalid critique JSON"],
                "must_fix": [],
            }
        critique = normalize_critique(critique)

        critique_file = workspace_path / f"solution_critique_pass_{pass_num}.json"
        critique_file.write_text(json.dumps(critique, indent=2) + "\n", encoding="utf-8")
        critique_history.append(critique)

        if is_critique_approved(critique):
            approved = True
            logger.info(
                "Solution critique approved on pass %d (iteration %d/%d)",
                pass_num,
                passes_run,
                max_passes,
            )
            break

        if passes_run >= max_passes:
            approved = False
            logger.warning(
                "Solution critique not approved after %d pass(es) (max_passes=%d); "
                "must_fix=%r",
                existing_passes + passes_run,
                max_passes,
                _non_empty_must_fix(critique),
            )
            break

        blockers = _non_empty_must_fix(critique)
        logger.info(
            "Solution pass %d blocked (%d must_fix) — continuing iteration %d/%d",
            pass_num,
            len(blockers),
            passes_run + 1,
            max_passes,
        )
        feedback = _format_critique_feedback(critique)

    total_pass_count = existing_passes + passes_run
    return approved, total_pass_count, critique_history


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

    approved, pass_count, critique_history = run_architect_critique_passes(
        vision,
        project_context,
        workspace_path,
        architect_agent,
        critique_agent,
        candidates_json,
        max_passes=max_passes,
        progress_callback=progress_callback,
    )

    profile = infer_capability_profile(vision)
    write_stack_manifest_from_solution_spec(
        vision,
        profile,
        workspace_path,
        spec_text=(
            spec_path.read_text(encoding="utf-8", errors="replace")
            if spec_path.exists()
            else ""
        ),
    )

    return SolutionResult(
        approved=approved,
        pass_count=pass_count,
        spec_path=spec_path,
        candidates_path=candidates_path,
        critique_history=critique_history,
        path="full",
        stack_manifest_path=workspace_path / STACK_MANIFEST_FILENAME,
    )
