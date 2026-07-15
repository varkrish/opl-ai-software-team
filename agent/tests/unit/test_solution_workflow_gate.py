"""Unit tests for solutioning workflow gate — TDD RED first."""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "agent" / "src"))

from crew_studio.job_database import JobDatabase
from llamaindex_crew.config.secure_config import PlanReviewConfig, SolutioningConfig
from llamaindex_crew.orchestrator.state_machine import ProjectState
from llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


@pytest.fixture
def job_db(tmp_path):
    db = JobDatabase(tmp_path / "jobs.db")
    return db


def _make_config(solutioning_enabled=False, plan_review_enabled=False):
    cfg = MagicMock()
    cfg.solutioning = SolutioningConfig(enabled=solutioning_enabled)
    cfg.plan_review = PlanReviewConfig(enabled=plan_review_enabled)
    cfg.prompt_limits = MagicMock(rag_chunk_size=1024, rag_chunk_overlap=128)
    cfg.epic = MagicMock(auto_approve_no_jira=True)
    return cfg


def _make_workflow(workspace, job_db, config=None, metadata=None):
    job_id = "job-sol-test"
    job_db.create_job(job_id, "Build an app", str(workspace))
    if metadata:
        job_db.update_job(job_id, {"metadata": json.dumps(metadata)})
    wf = SoftwareDevWorkflow(
        project_id=job_id,
        workspace_path=workspace,
        vision="Build an app",
        config=config or _make_config(),
        job_db=job_db,
    )
    return wf


class TestSolutioningEnabled:
    def test_solutioning_disabled_by_default(self, workspace, job_db):
        wf = _make_workflow(workspace, job_db)
        assert wf._solutioning_enabled() is False

    def test_solutioning_enabled_via_config(self, workspace, job_db):
        wf = _make_workflow(workspace, job_db, config=_make_config(solutioning_enabled=True))
        assert wf._solutioning_enabled() is True

    def test_solutioning_metadata_override_wins(self, workspace, job_db):
        wf = _make_workflow(
            workspace,
            job_db,
            config=_make_config(solutioning_enabled=True),
            metadata={"auto_approve_solution": True},
        )
        assert wf._solutioning_enabled() is False

    def test_solutioning_metadata_force_enable(self, workspace, job_db):
        wf = _make_workflow(
            workspace,
            job_db,
            config=_make_config(solutioning_enabled=False),
            metadata={"auto_approve_solution": False},
        )
        assert wf._solutioning_enabled() is True


def _parse_meta(job):
    meta = job.get("metadata") or {}
    if isinstance(meta, str):
        return json.loads(meta) if meta else {}
    return meta if isinstance(meta, dict) else {}


class TestPauseForSolutionReview:
    def test_pause_sets_pending_solution_review_status(self, workspace, job_db):
        wf = _make_workflow(workspace, job_db, config=_make_config(solutioning_enabled=True))
        wf._pause_for_solution_review()
        job = job_db.get_job(wf.project_id)
        assert job["status"] == "pending_solution_review"

    def test_pause_returns_dict_with_status(self, workspace, job_db):
        wf = _make_workflow(workspace, job_db, config=_make_config(solutioning_enabled=True))
        result = wf._pause_for_solution_review()
        assert result["status"] == "pending_solution_review"
        assert result["project_id"] == wf.project_id

    def test_pause_writes_solution_feedback_history(self, workspace, job_db):
        wf = _make_workflow(workspace, job_db, config=_make_config(solutioning_enabled=True))
        wf._pause_for_solution_review()
        meta = _parse_meta(job_db.get_job(wf.project_id))
        assert "solution_feedback_history" in meta
        assert meta["solution_feedback_history"] == []


class TestRunSolutionGate:
    def test_run_pauses_at_solution_gate(self, workspace, job_db):
        wf = _make_workflow(workspace, job_db, config=_make_config(solutioning_enabled=True))
        with patch.object(wf, "run_meta_phase"), patch.object(
            wf, "_run_solutioning_loop"
        ), patch.object(wf, "_run_phase_with_retry") as run_phase:
            result = wf.run()
        assert result["status"] == "pending_solution_review"
        run_phase.assert_not_called()

    def test_run_skips_solution_gate_when_approved(self, workspace, job_db):
        wf = _make_workflow(
            workspace,
            job_db,
            config=_make_config(solutioning_enabled=True),
            metadata={"solution_approved": True},
        )
        with patch.object(wf, "run_meta_phase"), patch.object(
            wf, "_run_solutioning_loop"
        ) as sol_loop, patch.object(wf, "_run_phase_with_retry") as run_phase, patch.object(
            wf, "_plan_review_enabled", return_value=True
        ), patch.object(
            wf, "_pause_for_plan_review",
            return_value={"status": "pending_review", "project_id": wf.project_id},
        ), patch.object(wf.state_machine, "transition"):
            result = wf.run()
        sol_loop.assert_not_called()
        assert run_phase.call_count >= 1
        assert result["status"] == "pending_review"

    def test_run_skips_solution_gate_when_disabled(self, workspace, job_db):
        wf = _make_workflow(workspace, job_db, config=_make_config(solutioning_enabled=False))
        with patch.object(wf, "run_meta_phase"), patch.object(
            wf, "_run_solutioning_loop"
        ) as sol_loop, patch.object(wf, "_run_phase_with_retry") as run_phase, patch.object(
            wf, "_plan_review_enabled", return_value=True
        ), patch.object(
            wf, "_pause_for_plan_review",
            return_value={"status": "pending_review", "project_id": wf.project_id},
        ), patch.object(wf.state_machine, "transition"):
            result = wf.run()
        sol_loop.assert_not_called()
        assert run_phase.call_count >= 1
        assert result["status"] == "pending_review"

    def test_solution_approve_does_not_skip_plan_review(self, workspace, job_db):
        wf = _make_workflow(
            workspace,
            job_db,
            config=_make_config(solutioning_enabled=True, plan_review_enabled=True),
            metadata={"solution_approved": True},
        )
        with patch.object(wf, "run_meta_phase"), patch.object(
            wf, "_run_solutioning_loop"
        ), patch.object(wf, "run_product_owner_phase", return_value="stories"), patch.object(
            wf, "run_designer_phase", return_value="design"
        ), patch.object(wf, "run_tech_architect_phase", return_value="stack"), patch.object(
            wf, "_run_phase_with_retry",
            side_effect=lambda phase, fn: fn(),
        ), patch.object(wf.state_machine, "transition"):
            result = wf.run()
        assert result["status"] == "pending_review"
        meta = _parse_meta(job_db.get_job(wf.project_id))
        assert meta.get("pending_review_approved") is not True


class TestPOContextInjection:
    def test_po_reads_solution_spec_when_exists(self, workspace, job_db):
        (workspace / "solution_spec.md").write_text(
            "# Solution\n\nUse microservices with FastAPI.", encoding="utf-8"
        )
        (workspace / "agent_backstories.json").write_text("{}", encoding="utf-8")
        wf = _make_workflow(workspace, job_db)
        wf.project_context = "Original context"
        wf.agent_backstories = {"product_owner": "PO backstory"}
        (workspace / "user_stories.md").write_text("x" * 200, encoding="utf-8")

        with patch.object(wf, "_report_progress"), patch(
            "llamaindex_crew.workflows.software_dev_workflow._check_vision_coherence",
            return_value=True,
        ), patch(
            "llamaindex_crew.workflows.software_dev_workflow._ensure_feature_files",
            return_value=1,
        ), patch.object(wf.document_indexer, "index_artifacts"), patch(
            "llamaindex_crew.workflows.software_dev_workflow.ProductOwnerAgent"
        ) as MockPO:
            instance = MagicMock()
            instance.run.return_value = "user stories content " * 20
            instance.supports_react = True
            MockPO.return_value = instance
            with patch(
                "llamaindex_crew.workflows.software_dev_workflow._persist_phase_artifact"
            ), patch(
                "llamaindex_crew.workflows.software_dev_workflow._is_agent_summary",
                return_value=False,
            ):
                wf.run_product_owner_phase()

        assert "SOLUTION SPECIFICATION" in (wf.project_context or "")

    def test_po_works_without_solution_spec(self, workspace, job_db):
        wf = _make_workflow(workspace, job_db)
        wf.project_context = "Original context"
        wf.agent_backstories = {"product_owner": "PO backstory"}
        original = wf.project_context
        (workspace / "user_stories.md").write_text("x" * 200, encoding="utf-8")

        with patch.object(wf, "_report_progress"), patch(
            "llamaindex_crew.workflows.software_dev_workflow._check_vision_coherence",
            return_value=True,
        ), patch(
            "llamaindex_crew.workflows.software_dev_workflow._ensure_feature_files",
            return_value=1,
        ), patch.object(wf.document_indexer, "index_artifacts"), patch(
            "llamaindex_crew.workflows.software_dev_workflow.ProductOwnerAgent"
        ) as MockPO:
            instance = MagicMock()
            instance.run.return_value = "user stories content " * 20
            instance.supports_react = True
            MockPO.return_value = instance
            with patch(
                "llamaindex_crew.workflows.software_dev_workflow._persist_phase_artifact"
            ), patch(
                "llamaindex_crew.workflows.software_dev_workflow._is_agent_summary",
                return_value=False,
            ):
                wf.run_product_owner_phase()

        assert wf.project_context == original


class TestResumeCheckpoint:
    def test_infer_resume_with_solution_spec_only(self, workspace, job_db):
        (workspace / "agent_backstories.json").write_text("{}", encoding="utf-8")
        (workspace / "solution_spec.md").write_text("# spec", encoding="utf-8")
        wf = _make_workflow(workspace, job_db)
        state = wf._infer_resume_state_from_artifacts()
        assert state == ProjectState.PRODUCT_OWNER

    def test_infer_resume_with_user_stories_after_solution(self, workspace, job_db):
        (workspace / "solution_spec.md").write_text("# spec", encoding="utf-8")
        (workspace / "user_stories.md").write_text("# stories", encoding="utf-8")
        wf = _make_workflow(workspace, job_db)
        state = wf._infer_resume_state_from_artifacts()
        assert state == ProjectState.DESIGNER

    def test_load_artifacts_includes_solution_spec(self, workspace, job_db):
        spec_text = "# Solution spec content"
        (workspace / "solution_spec.md").write_text(spec_text, encoding="utf-8")
        wf = _make_workflow(workspace, job_db)
        wf._load_phase_artifacts()
        assert wf.solution_spec == spec_text
