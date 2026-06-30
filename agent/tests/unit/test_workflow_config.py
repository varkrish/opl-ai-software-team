"""Unit tests for workflow_config merge helpers."""
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(root))

from crew_studio.workflow_config import merge_workflow_prefs_into_config, normalize_workflow_prefs
from src.llamaindex_crew.config import ConfigLoader


def test_normalize_workflow_prefs_clamps_values():
    raw = {
        "solutioning_max_passes": 99,
        "solutioning_max_github_searches": 0,
        "plan_review_enabled": 1,
    }
    out = normalize_workflow_prefs(raw)
    assert out["solutioning_max_passes"] == 5
    assert out["solutioning_max_github_searches"] == 1
    assert out["plan_review_enabled"] is True


def test_merge_workflow_prefs_into_config():
    try:
        config = ConfigLoader.load()
    except Exception:
        from src.llamaindex_crew.config.secure_config import SecretConfig, LLMConfig
        config = SecretConfig(llm=LLMConfig(api_key="test-key"))

    merged = merge_workflow_prefs_into_config(config, {
        "plan_review_enabled": True,
        "solutioning_enabled": True,
        "solutioning_max_passes": 2,
        "solutioning_max_github_searches": 7,
    })
    assert merged.plan_review.enabled is True
    assert merged.solutioning.enabled is True
    assert merged.solutioning.max_passes == 2
    assert merged.solutioning.max_github_searches == 7
