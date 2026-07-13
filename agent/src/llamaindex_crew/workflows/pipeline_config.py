"""Backward-compatible re-exports — prefer workflow_resolver."""
from __future__ import annotations

from typing import Any, Dict, Optional

from .workflow_resolver import (
    FALLBACK_PIPELINES as DEFAULT_WORKFLOW_PIPELINES,
    flatten_pipeline,
    is_feature_by_feature_pipeline,
    is_tdd_pipeline,
    last_planning_phase,
    phase_after,
    project_state_for_phase,
    resolve_solutioning_path,
    resolve_workflow_pipeline,
    resume_phase_after_plan_review,
    workflows_from_config,
)

# Legacy name used in a few call sites
WORKFLOW_TDD_ENABLED: Dict[str, bool] = {}


def resolve_pipeline(metadata: Optional[Dict[str, Any]] = None):
    """Legacy alias — uses stored phases or YAML fallback (no smart_router)."""
    return resolve_workflow_pipeline(metadata, vision="", config=None, force_refresh=False)


def is_tdd_enabled(metadata: Optional[Dict[str, Any]] = None) -> bool:
    """Legacy alias — TDD inferred from pipeline structure."""
    return is_tdd_pipeline(resolve_pipeline(metadata))
