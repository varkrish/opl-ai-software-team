"""Work-intent routing metadata for Crew jobs."""
from __future__ import annotations

from typing import Any, Optional

WORK_INTENT_DELIVER_EPIC = "deliver_epic"
WORK_INTENT_DELIVER_BUILD = "deliver_build"
WORK_INTENT_FIX = "fix"
WORK_INTENT_TRANSFORM = "transform"
WORK_INTENT_CHANGE = "change"
WORK_INTENT_REPLAN = "replan"


def apply_fix_mode_metadata(metadata: Optional[dict] = None, *, auto_fix: bool = True) -> dict:
    """Normalize metadata for mode=fix (import + auto fix refine)."""
    meta = dict(metadata or {})
    meta["job_mode"] = "import"
    meta["work_intent"] = WORK_INTENT_FIX
    meta.setdefault("refinement_kind", "fix")
    meta["auto_fix_after_analyze"] = auto_fix
    return meta


def parse_metadata(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        import json
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}
