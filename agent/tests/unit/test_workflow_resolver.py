"""Unit tests for workflow_resolver pipeline resolution and resume navigation."""
from types import SimpleNamespace

from llamaindex_crew.workflows.workflow_resolver import (
    FALLBACK_PIPELINES,
    flatten_pipeline,
    is_feature_by_feature_pipeline,
    is_tdd_pipeline,
    phase_after,
    resume_phase_after_plan_review,
    resolve_workflow_pipeline,
)


def test_full_pipeline_has_qa_before_development():
    flat = flatten_pipeline(FALLBACK_PIPELINES["full"])
    assert flat.index("qa") < flat.index("development")


def test_phase_after_tech_architect_is_qa_for_full():
    pipeline = FALLBACK_PIPELINES["full"]
    assert phase_after(pipeline, "tech_architect") == "qa"


def test_resume_after_plan_review_full_path_is_qa():
    meta = {
        "capability_profile": {"solutioning_path": "full", "source": "user"},
        "selected_workflow_phases": FALLBACK_PIPELINES["full"],
    }
    assert resume_phase_after_plan_review(meta) == "qa"


def test_resume_after_plan_review_fast_path_is_development():
    meta = {
        "capability_profile": {"solutioning_path": "fast", "source": "user"},
        "selected_workflow_phases": FALLBACK_PIPELINES["fast"],
    }
    assert resume_phase_after_plan_review(meta) == "development"


def test_is_tdd_from_pipeline_structure_not_path_name():
    assert is_tdd_pipeline(FALLBACK_PIPELINES["full"]) is True
    assert is_tdd_pipeline(FALLBACK_PIPELINES["fast"]) is False
    custom_tdd = ["meta", "tech_architect", "qa", {"parallel": ["development"]}]
    assert is_tdd_pipeline(custom_tdd) is True


def test_is_feature_by_feature_when_po_in_pipeline():
    assert is_feature_by_feature_pipeline(FALLBACK_PIPELINES["full"]) is True
    assert is_feature_by_feature_pipeline(FALLBACK_PIPELINES["fast"]) is False


def test_resolve_uses_stored_phases_without_refresh():
    custom = ["meta", "tech_architect", "qa", {"parallel": ["development"]}]
    meta = {"selected_workflow_phases": custom}
    assert resolve_workflow_pipeline(meta, force_refresh=False) == custom


def test_resume_uses_stored_adaptive_pipeline_not_reroute():
    adaptive = ["meta", "stack_contract", "tech_architect", "qa", {"parallel": ["development"]}]
    meta = {
        "capability_profile": {"solutioning_path": "adaptive"},
        "selected_workflow_phases": adaptive,
    }
    assert resume_phase_after_plan_review(meta) == "qa"


def test_resolve_full_from_yaml_workflows():
    cfg = SimpleNamespace(
        workflows={
            "full": ["meta", "tech_architect", "qa", {"parallel": ["development"]}],
        },
    )
    meta = {"capability_profile": {"solutioning_path": "full"}}
    pipeline = resolve_workflow_pipeline(meta, config=cfg, force_refresh=True)
    assert pipeline == cfg.workflows["full"]
