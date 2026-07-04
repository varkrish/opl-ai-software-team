"""Merge per-user workflow preferences into SecretConfig at job runtime."""
from __future__ import annotations

from typing import Any, Dict, Optional

DEFAULT_WORKFLOW_PREFS: Dict[str, Any] = {
    "plan_review_enabled": False,
    "solutioning_enabled": False,
    "solutioning_max_passes": 3,
    "solutioning_max_github_searches": 10,
    "auto_approve_plan": False,
    "tldr_enabled": True,
    "tldr_max_chars": 6000,
    "tldr_include_structure": True,
    "tldr_min_completed_files": 1,
}


def normalize_workflow_prefs(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return a full prefs dict with defaults applied."""
    merged = dict(DEFAULT_WORKFLOW_PREFS)
    if not raw:
        return merged
    for key in DEFAULT_WORKFLOW_PREFS:
        if key in raw and raw[key] is not None:
            merged[key] = raw[key]
    merged["plan_review_enabled"] = bool(merged["plan_review_enabled"])
    merged["solutioning_enabled"] = bool(merged["solutioning_enabled"])
    merged["auto_approve_plan"] = bool(merged["auto_approve_plan"])
    merged["solutioning_max_passes"] = max(1, min(5, int(merged["solutioning_max_passes"])))
    merged["solutioning_max_github_searches"] = max(1, min(50, int(merged["solutioning_max_github_searches"])))
    merged["tldr_enabled"] = bool(merged["tldr_enabled"])
    merged["tldr_include_structure"] = bool(merged["tldr_include_structure"])
    merged["tldr_max_chars"] = max(500, min(50_000, int(merged["tldr_max_chars"])))
    merged["tldr_min_completed_files"] = max(0, min(100, int(merged["tldr_min_completed_files"])))
    return merged


def merge_workflow_prefs_into_config(config: Any, prefs: Optional[Dict[str, Any]]) -> Any:
    """Overlay user workflow prefs onto server SecretConfig (returns same object if no prefs)."""
    if config is None or not prefs:
        return config
    normalized = normalize_workflow_prefs(prefs)
    plan_review = config.plan_review.model_copy(
        update={"enabled": normalized["plan_review_enabled"]}
    )
    solutioning = config.solutioning.model_copy(
        update={
            "enabled": normalized["solutioning_enabled"],
            "max_passes": normalized["solutioning_max_passes"],
            "max_github_searches": normalized["solutioning_max_github_searches"],
        }
    )
    generation = config.generation.model_copy(
        update={
            "simple_mode_tldr_enabled": normalized["tldr_enabled"],
            "simple_mode_tldr_max_chars": normalized["tldr_max_chars"],
            "simple_mode_tldr_include_structure": normalized["tldr_include_structure"],
            "simple_mode_tldr_min_completed_files": normalized["tldr_min_completed_files"],
        }
    )
    return config.model_copy(
        update={
            "plan_review": plan_review,
            "solutioning": solutioning,
            "generation": generation,
        }
    )


def config_for_job_owner(config: Any, job_db: Any, owner_id: Optional[str]) -> Any:
    """Load owner workflow prefs from job_db and merge into config."""
    if not config or not job_db or not owner_id:
        return config
    prefs = job_db.get_workflow_config(owner_id)
    if not prefs:
        return config
    return merge_workflow_prefs_into_config(config, prefs)
