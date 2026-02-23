"""
TDD tests for retry-failed-only migration.

When restarting a migration job, only failed/stale tasks should be re-executed.
Completed tasks are preserved.
"""
import sys
import uuid
from pathlib import Path
from unittest.mock import patch, MagicMock, ANY

import pytest

root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "agent"))
sys.path.insert(0, str(root / "agent" / "src"))

from crew_studio.job_database import JobDatabase


def _make_db(tmp_path):
    return JobDatabase(tmp_path / "test.db")


def _create_job(job_db, status, phase, vision):
    job_id = str(uuid.uuid4())
    ws = f"/tmp/ws/job-{job_id}"
    job_db.create_job(job_id, vision, ws)
    job_db.update_job(job_id, {"status": status, "current_phase": phase})
    return job_id


def _add_issue(job_db, job_id, status, title="Test issue", files=None):
    """Helper to insert a migration_issue with a given status."""
    issue_id = f"mig-{uuid.uuid4().hex[:12]}"
    job_db.create_migration_issue(
        issue_id=issue_id,
        job_id=job_id,
        migration_id="mig-test",
        title=title,
        severity="mandatory",
        effort="low",
        files=files or ["src/Main.java"],
        description=f"Issue: {title}",
        migration_hint="Fix it",
    )
    if status != "pending":
        job_db.update_migration_issue_status(issue_id, status)
    return issue_id


# ═══════════════════════════════════════════════════════════════════════════════
# DB helpers
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetFailedMigrationIssues:
    def test_returns_only_failed_issues(self, tmp_path):
        job_db = _make_db(tmp_path)
        job_id = _create_job(job_db, "failed", "error", "[MTA] test.zip")
        _add_issue(job_db, job_id, "completed", "Done issue")
        failed_id = _add_issue(job_db, job_id, "failed", "Failed issue")
        _add_issue(job_db, job_id, "skipped", "Skipped issue")

        failed = job_db.get_failed_migration_issues(job_id)
        assert len(failed) == 1
        assert failed[0]["id"] == failed_id

    def test_returns_empty_when_no_failures(self, tmp_path):
        job_db = _make_db(tmp_path)
        job_id = _create_job(job_db, "completed", "completed", "[MTA] test.zip")
        _add_issue(job_db, job_id, "completed", "Done")

        failed = job_db.get_failed_migration_issues(job_id)
        assert failed == []


class TestResetMigrationIssuesForRetry:
    def test_resets_failed_to_pending(self, tmp_path):
        job_db = _make_db(tmp_path)
        job_id = _create_job(job_db, "failed", "error", "[MTA] test.zip")
        failed_id = _add_issue(job_db, job_id, "failed", "Failed issue")
        completed_id = _add_issue(job_db, job_id, "completed", "Done issue")

        count = job_db.reset_failed_migration_issues(job_id)
        assert count == 1

        issues = {i["id"]: i for i in job_db.get_migration_issues(job_id)}
        assert issues[failed_id]["status"] == "pending"
        assert issues[failed_id]["error"] is None
        assert issues[completed_id]["status"] == "completed"  # untouched


# ═══════════════════════════════════════════════════════════════════════════════
# Migration retry runner
# ═══════════════════════════════════════════════════════════════════════════════

class TestRunMigrationRetry:
    def test_retry_function_exists(self):
        from crew_studio.migration.runner import run_migration_retry
        assert callable(run_migration_retry)

    def test_retry_only_processes_failed_issues(self, tmp_path):
        """run_migration_retry should only re-execute the failed issues."""
        job_db = _make_db(tmp_path)
        job_id = _create_job(job_db, "failed", "error", "[MTA] test.zip")

        # Create workspace with a java file
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "src").mkdir()
        java_file = ws / "src" / "Main.java"
        java_file.write_text("public class Main {}")

        # One completed, one failed
        completed_id = _add_issue(job_db, job_id, "completed", "Done", files=["src/Done.java"])
        failed_id = _add_issue(job_db, job_id, "failed", "Failed", files=["src/Main.java"])

        from crew_studio.migration.runner import run_migration_retry
        with patch("llamaindex_crew.agents.migration_agent.MigrationExecutionAgent") as MockAgent:
            mock_instance = MockAgent.return_value
            mock_instance.run = MagicMock()
            with patch("crew_studio.migration.runner.workspace_has_changes", return_value=True), \
                 patch("crew_studio.migration.runner.git_snapshot"):
                run_migration_retry(
                    job_id=job_id,
                    workspace_path=str(ws),
                    migration_goal="Apply migration changes",
                    job_db=job_db,
                )

        # The completed issue should still be completed
        issues = {i["id"]: i for i in job_db.get_migration_issues(job_id)}
        assert issues[completed_id]["status"] == "completed"
        # The failed issue should now be completed (agent ran successfully)
        assert issues[failed_id]["status"] == "completed"


# ═══════════════════════════════════════════════════════════════════════════════
# POST /restart for migration uses retry logic
# ═══════════════════════════════════════════════════════════════════════════════

class TestRestartEndpointRetrysMigration:
    def test_restart_migration_retries_failed_only(self, tmp_path):
        """POST /restart on a migration job should use retry_failed mode."""
        job_db = _make_db(tmp_path)
        job_id = _create_job(job_db, "failed", "migration_failed", "[MTA] legacy.zip")
        _add_issue(job_db, job_id, "completed", "Done issue")
        _add_issue(job_db, job_id, "failed", "Failed issue")

        from crew_studio.llamaindex_web_app import app
        with app.test_client() as client:
            with patch("crew_studio.llamaindex_web_app.job_db", job_db), \
                 patch("crew_studio.llamaindex_web_app.threading.Thread") as MockThread:
                MockThread.return_value.start = MagicMock()
                resp = client.post(f"/api/jobs/{job_id}/restart")

        assert resp.status_code == 202
        data = resp.get_json()
        assert data["job_type"] == "migration"
        assert data["mode"] == "retry_failed"
        assert data["failed_issues"] == 1
        # Thread should have been spawned to run retry
        MockThread.assert_called_once()
        MockThread.return_value.start.assert_called_once()

    def test_restart_migration_with_no_failures_still_succeeds(self, tmp_path):
        """If all issues are completed, restart still works (no-op migration)."""
        job_db = _make_db(tmp_path)
        job_id = _create_job(job_db, "failed", "migration_failed", "[MTA] legacy.zip")
        _add_issue(job_db, job_id, "completed", "Done")

        from crew_studio.llamaindex_web_app import app
        with app.test_client() as client:
            with patch("crew_studio.llamaindex_web_app.job_db", job_db), \
                 patch("crew_studio.llamaindex_web_app.threading.Thread") as MockThread:
                MockThread.return_value.start = MagicMock()
                resp = client.post(f"/api/jobs/{job_id}/restart")

        assert resp.status_code == 202
