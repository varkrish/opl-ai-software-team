"""
TDD tests for startup job resumption.

At server startup:
- Build jobs that were running/queued (not awaiting_migration/refactor) are re-started.
- Migration/refactor jobs that were running are marked as interrupted (failed).
- Refinements that were running are marked as failed.
- Completed/failed/cancelled jobs are not touched.
"""
import sys
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest

root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "agent"))
sys.path.insert(0, str(root / "agent" / "src"))

from crew_studio.job_database import JobDatabase


def _make_db(tmp_path):
    """Create a fresh test DB."""
    db_path = tmp_path / "test.db"
    return JobDatabase(db_path)


def _create_job(job_db, status, current_phase, vision="Test vision"):
    """Helper to create a job with a given status and phase."""
    job_id = str(uuid.uuid4())
    ws = f"/tmp/ws/job-{job_id}"
    job_db.create_job(job_id, vision, ws)
    job_db.update_job(job_id, {"status": status, "current_phase": current_phase})
    return job_id


# ---------------------------------------------------------------------------
# Test: resume_pending_jobs exists and is callable
# ---------------------------------------------------------------------------

class TestResumePendingJobsExists:
    def test_resume_pending_jobs_is_importable(self):
        from crew_studio.llamaindex_web_app import resume_pending_jobs
        assert callable(resume_pending_jobs)


# ---------------------------------------------------------------------------
# Test: build jobs are resumed
# ---------------------------------------------------------------------------

class TestResumeBuildJobs:
    def test_running_build_job_is_restarted(self, tmp_path):
        """A build job with status=running, phase=meta should be re-started via run_job_async."""
        job_db = _make_db(tmp_path)
        job_id = _create_job(job_db, "running", "meta", "Build a REST API")

        from crew_studio.llamaindex_web_app import resume_pending_jobs
        with patch("crew_studio.llamaindex_web_app.threading.Thread") as MockThread:
            resume_pending_jobs(job_db)
            MockThread.assert_called()
            # Thread should be started with run_job_async and matching job_id
            thread_call = MockThread.call_args
            assert thread_call[1]["args"][0] == job_id

    def test_queued_build_job_is_started(self, tmp_path):
        """A build job with status=queued, phase=starting should be started."""
        job_db = _make_db(tmp_path)
        job_id = _create_job(job_db, "queued", "starting", "Build calculator")

        from crew_studio.llamaindex_web_app import resume_pending_jobs
        with patch("crew_studio.llamaindex_web_app.threading.Thread") as MockThread:
            resume_pending_jobs(job_db)
            MockThread.assert_called()
            thread_call = MockThread.call_args
            assert thread_call[1]["args"][0] == job_id


# ---------------------------------------------------------------------------
# Test: awaiting_migration / awaiting_refactor jobs are NOT resumed
# ---------------------------------------------------------------------------

class TestSkipAwaitingJobs:
    def test_awaiting_migration_not_resumed(self, tmp_path):
        """Jobs awaiting user-triggered migration should NOT be auto-started."""
        job_db = _make_db(tmp_path)
        _create_job(job_db, "queued", "awaiting_migration")

        from crew_studio.llamaindex_web_app import resume_pending_jobs
        with patch("crew_studio.llamaindex_web_app.threading.Thread") as MockThread:
            resume_pending_jobs(job_db)
            MockThread.assert_not_called()

    def test_awaiting_refactor_not_resumed(self, tmp_path):
        """Jobs awaiting user-triggered refactor should NOT be auto-started."""
        job_db = _make_db(tmp_path)
        _create_job(job_db, "queued", "awaiting_refactor")

        from crew_studio.llamaindex_web_app import resume_pending_jobs
        with patch("crew_studio.llamaindex_web_app.threading.Thread") as MockThread:
            resume_pending_jobs(job_db)
            MockThread.assert_not_called()


# ---------------------------------------------------------------------------
# Test: interrupted migration/refactor jobs are marked failed
# ---------------------------------------------------------------------------

class TestMarkInterruptedJobs:
    def test_running_migration_marked_interrupted(self, tmp_path):
        """A migration job that was running is marked failed with restart message."""
        job_db = _make_db(tmp_path)
        job_id = _create_job(job_db, "running", "migrating")

        from crew_studio.llamaindex_web_app import resume_pending_jobs
        with patch("crew_studio.llamaindex_web_app.threading.Thread"):
            resume_pending_jobs(job_db)

        job = job_db.get_job(job_id)
        assert job["status"] == "failed"
        assert "server restart" in job["error"].lower()

    def test_running_refactor_marked_interrupted(self, tmp_path):
        """A refactor job that was running is marked failed with restart message."""
        job_db = _make_db(tmp_path)
        job_id = _create_job(job_db, "running", "execution")  # refactor phase

        from crew_studio.llamaindex_web_app import resume_pending_jobs
        with patch("crew_studio.llamaindex_web_app.threading.Thread"):
            resume_pending_jobs(job_db)

        job = job_db.get_job(job_id)
        assert job["status"] == "failed"
        assert "server restart" in job["error"].lower()

    def test_running_refactor_design_phase_marked_interrupted(self, tmp_path):
        """Refactor in design phase is also marked interrupted."""
        job_db = _make_db(tmp_path)
        job_id = _create_job(job_db, "running", "refactoring")

        from crew_studio.llamaindex_web_app import resume_pending_jobs
        with patch("crew_studio.llamaindex_web_app.threading.Thread"):
            resume_pending_jobs(job_db)

        job = job_db.get_job(job_id)
        assert job["status"] == "failed"
        assert "server restart" in job["error"].lower()


# ---------------------------------------------------------------------------
# Test: stuck refinements are marked failed
# ---------------------------------------------------------------------------

class TestMarkStuckRefinements:
    def test_running_refinement_marked_failed(self, tmp_path):
        """A refinement with status=running should be marked failed at startup."""
        job_db = _make_db(tmp_path)
        job_id = _create_job(job_db, "completed", "completed", "Build API")
        # Simulate a refinement that was running when server died
        job_db.update_job(job_id, {"status": "running", "current_phase": "refining"})
        ref_id = str(uuid.uuid4())
        job_db.create_refinement(ref_id, job_id, "Add comments")

        from crew_studio.llamaindex_web_app import resume_pending_jobs
        with patch("crew_studio.llamaindex_web_app.threading.Thread"):
            resume_pending_jobs(job_db)

        # Refinement should be marked failed
        history = job_db.get_refinement_history(job_id)
        ref = [r for r in history if r["id"] == ref_id][0]
        assert ref["status"] == "failed"
        assert "server restart" in ref["error"].lower()

        # Job should be restored to completed (not left as running)
        job = job_db.get_job(job_id)
        assert job["status"] == "completed"


# ---------------------------------------------------------------------------
# Test: completed/failed/cancelled jobs are NOT touched
# ---------------------------------------------------------------------------

class TestDoNotTouchTerminalJobs:
    def test_completed_job_not_touched(self, tmp_path):
        job_db = _make_db(tmp_path)
        job_id = _create_job(job_db, "completed", "completed")

        from crew_studio.llamaindex_web_app import resume_pending_jobs
        with patch("crew_studio.llamaindex_web_app.threading.Thread") as MockThread:
            resume_pending_jobs(job_db)
            MockThread.assert_not_called()

        job = job_db.get_job(job_id)
        assert job["status"] == "completed"

    def test_failed_job_not_touched(self, tmp_path):
        job_db = _make_db(tmp_path)
        job_id = _create_job(job_db, "failed", "error")

        from crew_studio.llamaindex_web_app import resume_pending_jobs
        with patch("crew_studio.llamaindex_web_app.threading.Thread") as MockThread:
            resume_pending_jobs(job_db)
            MockThread.assert_not_called()

    def test_cancelled_job_not_touched(self, tmp_path):
        job_db = _make_db(tmp_path)
        job_id = _create_job(job_db, "cancelled", "cancelled")

        from crew_studio.llamaindex_web_app import resume_pending_jobs
        with patch("crew_studio.llamaindex_web_app.threading.Thread") as MockThread:
            resume_pending_jobs(job_db)
            MockThread.assert_not_called()
