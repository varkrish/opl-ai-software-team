"""
Resolve workflow pipelines from job metadata, YAML config, and smart_router.

Priority:
  1. ``metadata["selected_workflow_phases"]`` when already persisted on the job
  2. ``config.workflows[path]`` from ~/.crew-ai/config.yaml (or crew.config.yaml)
  3. :data:`FALLBACK_PIPELINES` built-in defaults
  4. For ``solutioning_path == "adaptive"`` only: :func:`smart_router.decide_workflow_phases`
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

PipelineItem = Union[str, Dict[str, Any]]
Pipeline = List[PipelineItem]

# Built-in defaults when YAML ``workflows:`` is empty (mirrors historical software_dev_workflow).
FALLBACK_PIPELINES: Dict[str, Pipeline] = {
    "full": [
        "meta",
        "stack_contract",
        "product_owner",
        "designer",
        "tech_architect",
        "qa",
        {"parallel": ["development", "frontend", "devops"]},
    ],
    "fast": [
        "meta",
        "stack_contract",
        "seed_minimal_artifacts",
        {"parallel": ["development", "frontend"]},
    ],
    "edit": ["meta", "qa", "refinement"],
    "refactor": ["meta", "stack_contract", "tech_architect", "qa", "refinement"],
}

_BUILD_PHASES = frozenset({"development", "frontend", "devops", "refinement"})
_PLANNING_PHASES = frozenset({
    "meta",
    "stack_contract",
    "product_owner",
    "designer",
    "tech_architect",
    "seed_minimal_artifacts",
    "qa",
})


def flatten_pipeline(pipeline: Pipeline) -> List[str]:
    """Expand nested parallel blocks into a flat ordered phase list."""
    flat: List[str] = []
    for item in pipeline:
        if isinstance(item, str):
            flat.append(item)
        elif isinstance(item, dict) and "parallel" in item:
            flat.extend(item["parallel"])
    return flat


def phase_after(pipeline: Pipeline, phase_name: str) -> Optional[str]:
    """Return the next phase after *phase_name* in *pipeline*, or None."""
    flat = flatten_pipeline(pipeline)
    try:
        idx = flat.index(phase_name)
    except ValueError:
        return None
    if idx + 1 < len(flat):
        return flat[idx + 1]
    return None


def last_planning_phase(pipeline: Pipeline) -> Optional[str]:
    """Last planning/setup phase before the first build parallel block."""
    flat = flatten_pipeline(pipeline)
    for name in reversed(flat):
        if name not in _BUILD_PHASES:
            return name
    return flat[-1] if flat else None


def resolve_solutioning_path(metadata: Optional[Dict[str, Any]]) -> str:
    """Read solutioning_path from job metadata capability_profile."""
    meta = metadata or {}
    profile = meta.get("capability_profile") or {}
    if isinstance(profile, str):
        path = profile.strip().lower()
    elif isinstance(profile, dict):
        path = str(profile.get("solutioning_path") or "adaptive").strip().lower()
    else:
        path = "adaptive"
    if path not in ("full", "fast", "adaptive", "edit", "refactor"):
        return "adaptive"
    return path


def workflows_from_config(config: Any) -> Dict[str, Pipeline]:
    """YAML ``workflows`` section or built-in fallbacks."""
    if config is not None and hasattr(config, "workflows"):
        raw = getattr(config, "workflows", None)
        if isinstance(raw, dict) and raw:
            return raw
    return FALLBACK_PIPELINES


def resolve_workflow_pipeline(
    metadata: Optional[Dict[str, Any]],
    vision: str = "",
    config: Any = None,
    budget_tracker=None,
    *,
    force_refresh: bool = False,
) -> Pipeline:
    """
    Resolve the phase pipeline for a job.

    When ``selected_workflow_phases`` is already on the job metadata and
    *force_refresh* is false, returns that list unchanged (approve/resume
    must follow the pipeline the job already committed to).

    Adaptive path invokes smart_router only when no persisted pipeline exists.
    """
    meta = metadata or {}
    if not force_refresh:
        stored = meta.get("selected_workflow_phases")
        if isinstance(stored, list) and stored:
            return stored

    path = resolve_solutioning_path(meta)
    yaml_map = workflows_from_config(config)

    if path == "adaptive":
        from .smart_router import decide_workflow_phases

        pipeline = decide_workflow_phases(vision, budget_tracker)
        logger.info("Resolved adaptive pipeline via smart_router (%d steps)", len(pipeline))
        return pipeline

    pipeline = yaml_map.get(path) or yaml_map.get("full") or FALLBACK_PIPELINES["full"]
    logger.info("Resolved %s pipeline from config/YAML fallback (%d steps)", path, len(pipeline))
    return list(pipeline)


def is_tdd_pipeline(pipeline: Pipeline) -> bool:
    """
    True when ``qa`` runs before the first build phase — test-first ordering.

    Derived from pipeline structure (smart_router / YAML / stored phases),
    not from a hardcoded path name.
    """
    flat = flatten_pipeline(pipeline)
    if "qa" not in flat:
        return False
    qa_idx = flat.index("qa")
    build_indices = [flat.index(p) for p in _BUILD_PHASES if p in flat]
    if not build_indices:
        return True
    return qa_idx < min(build_indices)


def is_feature_by_feature_pipeline(pipeline: Pipeline) -> bool:
    """
    True when the pipeline includes PO/BDD planning — dev should work feature-by-feature.
    """
    flat = flatten_pipeline(pipeline)
    return "product_owner" in flat


def resume_phase_after_plan_review(
    metadata: Optional[Dict[str, Any]],
    vision: str = "",
    config: Any = None,
    budget_tracker=None,
) -> str:
    """
    Next phase after human plan approval — walks the job's resolved pipeline.

    Plan review always pauses immediately after ``tech_architect`` when present,
    otherwise after the last planning phase (e.g. ``seed_minimal_artifacts`` on fast).
    """
    pipeline = resolve_workflow_pipeline(
        metadata, vision, config, budget_tracker, force_refresh=False,
    )
    flat = flatten_pipeline(pipeline)
    anchor = "tech_architect" if "tech_architect" in flat else last_planning_phase(pipeline)
    if anchor:
        nxt = phase_after(pipeline, anchor)
        if nxt:
            return nxt
    for name in flat:
        if name in _BUILD_PHASES:
            return name
    return "development"


def project_state_for_phase(phase_name: str) -> Optional[str]:
    """Map a pipeline phase string to ProjectState enum value when defined."""
    _KNOWN = {
        "meta",
        "product_owner",
        "designer",
        "tech_architect",
        "qa",
        "development",
        "frontend",
        "devops",
        "refinement",
    }
    return phase_name if phase_name in _KNOWN else None
