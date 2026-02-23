"""
TDD tests for the Restart Job feature.

Covers:
- DB helper: fail_stale_migrations(job_id)
- POST /api/jobs/<job_id>/restart endpoint (build, migration, refactor)
- Guard conditions (running, completed, nonexistent)
- resume_pending_jobs clearing stale migration_issues
"""
import sys
import uuid
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "agent"))
sys.path.insert(0, str(root / "agent" / "src"))

from crew_studio.job_database import JobDatabase


def _make_db(tmp_path):
    """Create a fresh test DB."""
    return JobDatabase(tmp_path / "test.db")


def _create_job(job_db, status, current_phase, vision="Test vision"):
    """Helper to create a job with a given status and phase."""
    job_id = str(uuid.uuid4())
    ws = f"/tmp/ws/job-{job_id}"
    job_db.create_job(job_id, vision, ws)
    job_db.update_job(job_id, {"status": status, "current_phase": current_phase})
    return job_id


def _create_migration_issue(job_db, job_id, status="running"):
    """Helper to create a migration_issue for a job."""
    issue_id = f"mig-{uuid.uuid4().hex[:12]}"
    job_db.create_migration_issue(
        issue_id=issue_id,
        job_id=job_id,
        migration_id="mig-test",
        title="Test issue",
        severity="mandatory",
        effort="low",
        files=["src/Main.java"],
        description="Test migration issue",
        migration_hint="Fix it",
    )
    if status != "pending":
        job_db.update_migration_issue_status(issue_id, status)
    return issue_id


# ═══════════════════════════════════════════════════════════════════════════════
# DB helper: fail_stale_migrations
# ═══════════════════════════════════════════════════════════════════════════════

class TestFailStaleMigrations:
    def test_clears_running_issues(self, tmp_path):
        """Running migration_issues are set to failed."""
        job_db = _make_db(tmp_path)
        job_id = _create_job(job_db, "failed", "error", "[MTA] test.zip")
        issue_id = _create_migration_issue(job_db, job_id, "running")

        job_db.fail_stale_migrations(job_id)

        issues = job_db.get_migration_issues(job_id)
        issue = [i for i in issues if i["id"] == issue_id][0]
        assert issue["status"] == "failed"
        assert "stale" in issue["error"].lower() or "restart" in issue["error"].lower()

    def test_returns_count(self, tmp_path):
        """Returns the number of rows updated."""
        job_db = _make_db(tmp_path)
        job_id = _create_job(job_db, "failed", "error", "[MTA] test.zip")
        _create_migration_issue(job_db, job_id, "running")
        _create_migration_issue(job_db, job_id, "running")

        count = job_db.fail_stale_migrations(job_id)
        assert count == 2

    def test_ignores_completed(self, tmp_path):
        """Does not touch already-completed issues."""
        job_db = _make_db(tmp_path)
        job_id = _create_job(job_db, "failed", "error", "[MTA] test.zip")
        completed_id = _create_migration_issue(job_db, job_id, "completed")
        running_id = _create_migration_issue(job_db, job_id, "running")

        count = job_db.fail_stale_migrations(job_id)
        assert count == 1

        issues = {i["id"]: i for i in job_db.get_migration_issues(job_id)}
        assert issues[completed_id]["status"] == "completed"
        assert issues[running_id]["status"] == "failed"


# ═══════════════════════════════════════════════════════════════════════════════
# POST /api/jobs/<job_id>/restart endpoint
# ═══════════════════════════════════════════════════════════════════════════════

class TestRestartEndpoint:
    def test_restart_build_job_resets_and_spawns_thread(self, tmp_path):
        """Failed build job: reset to queued, spawn run_job_async."""
        job_db = _make_db(tmp_path)
        job_id = _create_job(job_db, "failed", "error", "Build a REST API")

        from crew_studio.llamaindex_web_app import app
        with app.test_client() as client:
            with patch("crew_studio.llamaindex_web_app.job_db", job_db), \
                 patch("crew_studio.llamaindex_web_app.threading.Thread") as MockThread:
                MockThread.return_value.start = MagicMock()
                resp = client.post(f"/api/jobs/{job_id}/restart")

        assert resp.status_code == 202
        data = resp.get_json()
        assert data["job_type"] == "build"
        # Thread should have been started with run_job_async
        MockThread.assert_called_once()
        MockThread.return_value.start.assert_called_once()

    def test_restart_migration_job_clears_stale_and_starts(self, tmp_path):
        """Failed MTA job: clear stale migration_issues, start migration."""
        job_db = _make_db(tmp_path)
        job_id = _create_job(job_db, "failed", "migration_failed", "[MTA] legacy.zip")
        _create_migration_issue(job_db, job_id, "running")

        from crew_studio.llamaindex_web_app import app
        with app.test_client() as client:
            with patch("crew_studio.llamaindex_web_app.job_db", job_db), \
                 patch("crew_studio.llamaindex_web_app.threading.Thread") as MockThread:
                MockThread.return_value.start = MagicMock()
                resp = client.post(f"/api/jobs/{job_id}/restart")

        assert resp.status_code == 202
        data = resp.get_json()
        assert data["job_type"] == "migration"
        # Stale migration issue should be cleaned up
        assert job_db.get_running_migration(job_id) is None

    def test_restart_refactor_job_resets_and_starts(self, tmp_path):
        """Failed refactor job: reset status, start refactor thread."""
        job_db = _make_db(tmp_path)
        job_id = _create_job(job_db, "failed", "refactor_failed", "Refactor to Quarkus")

        from crew_studio.llamaindex_web_app import app
        with app.test_client() as client:
            with patch("crew_studio.llamaindex_web_app.job_db", job_db), \
                 patch("crew_studio.llamaindex_web_app.threading.Thread") as MockThread:
                MockThread.return_value.start = MagicMock()
                resp = client.post(f"/api/jobs/{job_id}/restart")

        assert resp.status_code == 202
        data = resp.get_json()
        assert data["job_type"] == "refactor"

    def test_restart_running_job_rejected(self, tmp_path):
        """Running job should return 400."""
        job_db = _make_db(tmp_path)
        job_id = _create_job(job_db, "running", "meta", "Build something")

        from crew_studio.llamaindex_web_app import app
        with app.test_client() as client:
            with patch("crew_studio.llamaindex_web_app.job_db", job_db):
                resp = client.post(f"/api/jobs/{job_id}/restart")

        assert resp.status_code == 400
        assert "not restartable" in resp.get_json()["error"].lower() or "running" in resp.get_json()["error"].lower()

    def test_restart_completed_job_accepted(self, tmp_path):
        """Completed job can be restarted (returns 202)."""
        job_db = _make_db(tmp_path)
        job_id = _create_job(job_db, "completed", "completed", "Build something")

        from crew_studio.llamaindex_web_app import app
        with app.test_client() as client:
            with patch("crew_studio.llamaindex_web_app.job_db", job_db), \
                 patch("crew_studio.llamaindex_web_app.threading.Thread") as MockThread:
                MockThread.return_value.start = MagicMock()
                resp = client.post(f"/api/jobs/{job_id}/restart")

        assert resp.status_code == 202

    def test_restart_nonexistent_job_404(self, tmp_path):
        """Unknown job_id should return 404."""
        job_db = _make_db(tmp_path)
        from crew_studio.llamaindex_web_app import app
        with app.test_client() as client:
            with patch("crew_studio.llamaindex_web_app.job_db", job_db):
                resp = client.post(f"/api/jobs/nonexistent-id/restart")

        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# resume_pending_jobs clears stale migration_issues
# ═══════════════════════════════════════════════════════════════════════════════

class TestResumePendingJobsClearsStaleMigrations:
    def test_resume_pending_jobs_clears_stale_migrations(self, tmp_path):
        """Startup hook should also clear orphaned migration_issues."""
        job_db = _make_db(tmp_path)
        job_id = _create_job(job_db, "running", "migrating", "[MTA] legacy.zip")
        issue_id = _create_migration_issue(job_db, job_id, "running")

        from crew_studio.llamaindex_web_app import resume_pending_jobs
        with patch("crew_studio.llamaindex_web_app.threading.Thread"):
            resume_pending_jobs(job_db)

        # Job should be marked failed
        job = job_db.get_job(job_id)
        assert job["status"] == "failed"

        # Stale migration_issue should also be failed
        assert job_db.get_running_migration(job_id) is None
        issues = job_db.get_migration_issues(job_id)
        issue = [i for i in issues if i["id"] == issue_id][0]
        assert issue["status"] == "failed"
