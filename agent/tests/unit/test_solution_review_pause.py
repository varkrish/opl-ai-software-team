"""Regression test: _run_job_async_impl must not mark a job completed when
run_build_pipeline returns status='pending_solution_review'.

Bug: the pause-handler only checked for 'pending_approval'/'pending_review',
so a solutioning pause fell through to mark_completed(), completing the job
instead of holding it for human review.
"""
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "agent" / "src"))


def _make_job(job_id: str, workspace: Path):
    return {
        "id": job_id,
        "current_phase": "queued",
        "status": "queued",
        "workspace_path": str(workspace),
        "metadata": "{}",
    }


def _call_run_job(job_id, workspace, pipeline_result):
    """Patch build_runner (inline import) and call _run_job_async_impl."""
    db = MagicMock()
    db.get_job.return_value = _make_job(job_id, workspace)
    db.get_job_documents.return_value = []

    # The inline `from crew_studio.build_runner import run_build_pipeline` resolves
    # through the module cache, so we patch there.
    import crew_studio.build_runner as _br
    original = getattr(_br, "run_build_pipeline", None)
    _br.run_build_pipeline = MagicMock(return_value=pipeline_result)
    try:
        with patch("crew_studio.llamaindex_web_app.job_db", db), \
             patch("crew_studio.llamaindex_web_app.config", MagicMock()):
            from crew_studio.llamaindex_web_app import _run_job_async_impl
            _run_job_async_impl(job_id, "Build a receipt capture app", MagicMock())
    finally:
        if original is not None:
            _br.run_build_pipeline = original
        else:
            del _br.run_build_pipeline

    return db


class TestSolutionReviewPause:
    """_run_job_async_impl pauses instead of completing on pending_solution_review."""

    def test_pending_solution_review_does_not_call_mark_completed(self, tmp_path):
        """When pipeline returns pending_solution_review, mark_completed must NOT be called."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        db = _call_run_job("sol-pause-001", workspace, {
            "status": "pending_solution_review",
            "budget_report": {},
        })
        db.mark_completed.assert_not_called()
        db.mark_failed.assert_not_called()
        db.mark_partially_completed.assert_not_called()

    def test_pending_solution_review_updates_job_status(self, tmp_path):
        """When pipeline returns pending_solution_review, the job DB is updated correctly."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        db = _call_run_job("sol-pause-002", workspace, {
            "status": "pending_solution_review",
        })
        update_calls = db.update_job.call_args_list
        pause_calls = [c for c in update_calls if c[0][1].get("status") == "pending_solution_review"]
        assert pause_calls, (
            f"Expected update_job(job_id, {{status: 'pending_solution_review', ...}}) "
            f"but got calls: {update_calls}"
        )

    def test_pending_solution_review_uses_progress_25(self, tmp_path):
        """pending_solution_review pause uses progress=25, not the 45 default used for plan review."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        db = _call_run_job("sol-pause-003", workspace, {"status": "pending_solution_review"})
        update_calls = db.update_job.call_args_list
        pause_call = next(
            (c for c in update_calls if c[0][1].get("status") == "pending_solution_review"),
            None,
        )
        assert pause_call is not None
        assert pause_call[0][1].get("progress") == 25

    def test_pending_approval_still_pauses(self, tmp_path):
        """Existing pending_approval behaviour is unaffected."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        db = _call_run_job("plan-pause-001", workspace, {"status": "pending_approval", "progress": 45})
        db.mark_completed.assert_not_called()
        db.mark_failed.assert_not_called()

    def test_pending_review_still_pauses(self, tmp_path):
        """Existing pending_review behaviour is unaffected."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        db = _call_run_job("plan-review-001", workspace, {"status": "pending_review", "progress": 50})
        db.mark_completed.assert_not_called()
        db.mark_failed.assert_not_called()


class TestSkipPhasesConstant:
    """_SKIP_PHASES must include pending_solution_review to prevent startup re-runs."""

    def test_pending_solution_review_in_skip_phases(self):
        from crew_studio.llamaindex_web_app import _SKIP_PHASES
        assert "pending_solution_review" in _SKIP_PHASES, (
            "pending_solution_review must be in _SKIP_PHASES so startup resume "
            "does not restart a job waiting for solution approval"
        )

    def test_resume_skips_pending_solution_review_jobs(self, tmp_path):
        """resume_pending_jobs must not re-trigger a pending_solution_review job."""
        from crew_studio.llamaindex_web_app import resume_pending_jobs

        db = MagicMock()
        db.get_all_jobs.return_value = [
            {
                "id": "sol-review-job-001",
                "status": "pending_solution_review",
                "current_phase": "pending_solution_review",
                "workspace_path": str(tmp_path / "ws"),
            }
        ]

        spawned_threads = []

        def capture_thread(**kwargs):
            t = MagicMock()
            spawned_threads.append(kwargs)
            return t

        with patch("crew_studio.llamaindex_web_app.threading.Thread", side_effect=capture_thread):
            resume_pending_jobs(override_job_db=db)

        db.mark_failed.assert_not_called()
        assert not spawned_threads, (
            f"resume_pending_jobs spawned a thread for a pending_solution_review job: {spawned_threads}"
        )
