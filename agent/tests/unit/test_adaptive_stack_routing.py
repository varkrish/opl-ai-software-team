"""Workflow routing for Full / Fast / Adaptive stack contract (TDD)."""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "agent" / "src"))

from crew_studio.job_database import JobDatabase
from llamaindex_crew.config.secure_config import PlanReviewConfig, SolutioningConfig
from llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow

MAP_VISION = (
    "Create a simple HTML page showing Asia Pacific region map with SVG, "
    "country labels and a colour legend"
)
FRAPPE_VISION = "Build a Frappe invoicing app with customer and invoice DocTypes"


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


@pytest.fixture
def job_db(tmp_path):
    return JobDatabase(tmp_path / "jobs.db")


def _make_config(solutioning_enabled=False):
    cfg = MagicMock()
    cfg.solutioning = SolutioningConfig(enabled=solutioning_enabled)
    cfg.plan_review = PlanReviewConfig(enabled=False)
    cfg.prompt_limits = MagicMock(rag_chunk_size=1024, rag_chunk_overlap=128)
    cfg.epic = MagicMock(auto_approve_no_jira=True)
    return cfg


def _make_workflow(workspace, job_db, vision=MAP_VISION, metadata=None, config=None):
    job_id = "job-route-test"
    job_db.create_job(job_id, vision, str(workspace))
    if metadata:
        job_db.update_job(job_id, {"metadata": json.dumps(metadata)})
    return SoftwareDevWorkflow(
        project_id=job_id,
        workspace_path=workspace,
        vision=vision,
        config=config or _make_config(),
        job_db=job_db,
    )


class TestResolveEffectiveSolutioningPath:
    def test_metadata_fast(self, workspace, job_db):
        wf = _make_workflow(
            workspace,
            job_db,
            metadata={"capability_profile": {"solutioning_path": "fast"}},
        )
        assert wf._resolve_effective_solutioning_path() == "fast"

    def test_metadata_full(self, workspace, job_db):
        wf = _make_workflow(
            workspace,
            job_db,
            metadata={"capability_profile": {"solutioning_path": "full"}},
        )
        assert wf._resolve_effective_solutioning_path() == "full"

    def test_metadata_adaptive_map_is_fast(self, workspace, job_db):
        wf = _make_workflow(
            workspace,
            job_db,
            vision=MAP_VISION,
            metadata={"capability_profile": {"solutioning_path": "adaptive"}},
        )
        assert wf._resolve_effective_solutioning_path() == "fast"

    def test_metadata_adaptive_frappe_is_full(self, workspace, job_db):
        wf = _make_workflow(
            workspace,
            job_db,
            vision=FRAPPE_VISION,
            metadata={"capability_profile": {"solutioning_path": "adaptive"}},
        )
        assert wf._resolve_effective_solutioning_path() == "full"

    def test_no_capability_profile_uses_config(self, workspace, job_db):
        wf = _make_workflow(
            workspace, job_db, config=_make_config(solutioning_enabled=True)
        )
        assert wf._resolve_effective_solutioning_path() == "fast"

    def test_no_capability_profile_disabled_skips(self, workspace, job_db):
        wf = _make_workflow(
            workspace, job_db, config=_make_config(solutioning_enabled=False)
        )
        assert wf._resolve_effective_solutioning_path() is None


class TestWorkflowStackRouting:
    def test_fast_calls_fast_writer_not_full_loop(self, workspace, job_db):
        wf = _make_workflow(
            workspace,
            job_db,
            metadata={"capability_profile": {"solutioning_path": "fast", "source": "user"}},
        )
        with patch.object(wf, "run_meta_phase"), patch(
            "llamaindex_crew.workflows.solutioning_loop.run_fast_stack_decision",
            wraps=__import__(
                "llamaindex_crew.workflows.solutioning_loop", fromlist=["run_fast_stack_decision"]
            ).run_fast_stack_decision,
        ) as fast_fn, patch(
            "llamaindex_crew.workflows.solutioning_loop.run_solutioning_loop"
        ) as full_fn, patch.object(wf, "_run_phase_with_retry") as run_phase, patch.object(
            wf,
            "_pause_for_plan_review",
            return_value={"status": "pending_review", "project_id": wf.project_id},
        ), patch.object(wf, "_plan_review_enabled", return_value=True), patch.object(
            wf.state_machine, "transition"
        ):
            result = wf.run()

        fast_fn.assert_called_once()
        full_fn.assert_not_called()
        assert (workspace / "stack_manifest.json").exists()
        assert (workspace / "solution_spec.md").exists()
        # Continues into planning (PO/Designer/TA) — does not pause for solution review
        assert result["status"] != "pending_solution_review"
        assert run_phase.call_count >= 1
        # TA phase still expected in the planning chain
        phases = [c.args[0] for c in run_phase.call_args_list]
        assert "tech_architect" in phases

    def test_full_calls_solutioning_loop(self, workspace, job_db):
        wf = _make_workflow(
            workspace,
            job_db,
            metadata={"capability_profile": {"solutioning_path": "full"}},
        )
        with patch.object(wf, "run_meta_phase"), patch.object(
            wf, "_run_solutioning_loop"
        ) as sol_loop, patch.object(wf, "_run_phase_with_retry") as run_phase:
            result = wf.run()

        sol_loop.assert_called_once()
        assert result["status"] == "pending_solution_review"
        run_phase.assert_not_called()

    def test_adaptive_map_uses_fast_writer(self, workspace, job_db):
        wf = _make_workflow(
            workspace,
            job_db,
            vision=MAP_VISION,
            metadata={"capability_profile": {"solutioning_path": "adaptive"}},
        )
        with patch.object(wf, "run_meta_phase"), patch(
            "llamaindex_crew.workflows.solutioning_loop.run_fast_stack_decision",
            wraps=__import__(
                "llamaindex_crew.workflows.solutioning_loop", fromlist=["run_fast_stack_decision"]
            ).run_fast_stack_decision,
        ) as fast_fn, patch(
            "llamaindex_crew.workflows.solutioning_loop.run_solutioning_loop"
        ) as full_fn, patch.object(wf, "_run_phase_with_retry") as run_phase, patch.object(
            wf,
            "_pause_for_plan_review",
            return_value={"status": "pending_review", "project_id": wf.project_id},
        ), patch.object(wf, "_plan_review_enabled", return_value=True), patch.object(
            wf.state_machine, "transition"
        ):
            wf.run()

        fast_fn.assert_called_once()
        full_fn.assert_not_called()
        assert (workspace / "stack_manifest.json").exists()
        assert run_phase.call_count >= 1
