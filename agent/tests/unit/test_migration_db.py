"""
Unit tests for migration-related methods in JobDatabase.
TDD: Written BEFORE implementation — these tests define the contract.
"""
import pytest
import tempfile
import json
from pathlib import Path

import sys
root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(root))

from crew_studio.job_database import JobDatabase


@pytest.fixture
def db():
    """Create a fresh in-memory-like temp DB for each test."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test_jobs.db"
        database = JobDatabase(db_path)
        # Create a job to attach migration issues to
        database.create_job("job-mig-1", "Migrate EAP 7 to 8", f"{tmp}/workspace")
        yield database


class TestMigrationIssuesTable:
    """Verify the migration_issues table exists and has the right schema."""

    def test_table_exists(self, db):
        """migration_issues table is created during _init_schema."""
        with db._get_conn() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='migration_issues'"
            ).fetchone()
            assert row is not None, "migration_issues table should exist"


class TestCreateMigrationIssue:
    """Verify create_migration_issue inserts and returns correctly."""

    def test_create_and_retrieve(self, db):
        """Insert a migration issue and retrieve it."""
        issue = db.create_migration_issue(
            issue_id="issue-001",
            job_id="job-mig-1",
            migration_id="mig-run-1",
            title="Replace javax with jakarta imports",
            severity="mandatory",
            effort="low",
            files=["src/UserService.java", "src/OrderService.java"],
            description="Change javax.inject to jakarta.inject",
            migration_hint="Replace import javax.inject.Inject with import jakarta.inject.Inject",
        )
        assert issue["id"] == "issue-001"
        assert issue["status"] == "pending"
        assert issue["job_id"] == "job-mig-1"
        assert issue["migration_id"] == "mig-run-1"
        assert issue["severity"] == "mandatory"
        assert issue["effort"] == "low"
        assert issue["title"] == "Replace javax with jakarta imports"

        # Retrieve via get_migration_issues
        issues = db.get_migration_issues("job-mig-1")
        assert len(issues) == 1
        assert issues[0]["id"] == "issue-001"
        # files should be stored as JSON and returned as a list
        files = json.loads(issues[0]["files"]) if isinstance(issues[0]["files"], str) else issues[0]["files"]
        assert "src/UserService.java" in files

    def test_create_multiple_issues(self, db):
        """Can insert multiple issues for the same migration run."""
        for i in range(5):
            db.create_migration_issue(
                issue_id=f"issue-{i:03d}",
                job_id="job-mig-1",
                migration_id="mig-run-1",
                title=f"Issue {i}",
                severity="mandatory" if i < 3 else "optional",
                effort="low",
                files=[f"src/File{i}.java"],
                description=f"Description {i}",
                migration_hint=f"Hint {i}",
            )
        issues = db.get_migration_issues("job-mig-1")
        assert len(issues) == 5


class TestUpdateMigrationIssueStatus:
    """Verify status transitions for migration issues."""

    def test_pending_to_running(self, db):
        db.create_migration_issue(
            issue_id="issue-010",
            job_id="job-mig-1",
            migration_id="mig-run-1",
            title="Test issue",
            severity="mandatory",
            effort="low",
            files=["src/App.java"],
            description="desc",
            migration_hint="hint",
        )
        result = db.update_migration_issue_status("issue-010", "running")
        assert result is True
        issues = db.get_migration_issues("job-mig-1")
        assert issues[0]["status"] == "running"

    def test_running_to_completed(self, db):
        db.create_migration_issue(
            issue_id="issue-011",
            job_id="job-mig-1",
            migration_id="mig-run-1",
            title="Test",
            severity="optional",
            effort="medium",
            files=[],
            description="d",
            migration_hint="h",
        )
        db.update_migration_issue_status("issue-011", "running")
        result = db.update_migration_issue_status("issue-011", "completed")
        assert result is True
        issues = db.get_migration_issues("job-mig-1")
        assert issues[0]["status"] == "completed"
        assert issues[0]["completed_at"] is not None

    def test_running_to_failed_with_error(self, db):
        db.create_migration_issue(
            issue_id="issue-012",
            job_id="job-mig-1",
            migration_id="mig-run-1",
            title="Fail test",
            severity="mandatory",
            effort="high",
            files=[],
            description="d",
            migration_hint="h",
        )
        db.update_migration_issue_status("issue-012", "running")
        result = db.update_migration_issue_status("issue-012", "failed", error="Agent timed out")
        assert result is True
        issues = db.get_migration_issues("job-mig-1")
        assert issues[0]["status"] == "failed"
        assert issues[0]["error"] == "Agent timed out"

    def test_update_nonexistent_issue_returns_false(self, db):
        result = db.update_migration_issue_status("nonexistent-id", "completed")
        assert result is False


class TestGetMigrationIssues:
    """Verify filtering and retrieval of migration issues."""

    def _seed(self, db):
        for i, mid in enumerate(["mig-A", "mig-A", "mig-B"]):
            db.create_migration_issue(
                issue_id=f"issue-f-{i}",
                job_id="job-mig-1",
                migration_id=mid,
                title=f"Issue {i}",
                severity="mandatory",
                effort="low",
                files=[],
                description="d",
                migration_hint="h",
            )

    def test_filter_by_job_id(self, db):
        self._seed(db)
        issues = db.get_migration_issues("job-mig-1")
        assert len(issues) == 3

    def test_filter_by_migration_id(self, db):
        self._seed(db)
        issues = db.get_migration_issues("job-mig-1", migration_id="mig-A")
        assert len(issues) == 2
        issues_b = db.get_migration_issues("job-mig-1", migration_id="mig-B")
        assert len(issues_b) == 1

    def test_empty_result_for_unknown_job(self, db):
        issues = db.get_migration_issues("nonexistent-job")
        assert issues == []


class TestGetMigrationSummary:
    """Verify aggregated counts by status."""

    def test_summary_counts(self, db):
        for i in range(6):
            db.create_migration_issue(
                issue_id=f"issue-s-{i}",
                job_id="job-mig-1",
                migration_id="mig-run-1",
                title=f"Issue {i}",
                severity="mandatory",
                effort="low",
                files=[],
                description="d",
                migration_hint="h",
            )
        # Move some to different statuses
        db.update_migration_issue_status("issue-s-0", "running")
        db.update_migration_issue_status("issue-s-1", "completed")
        db.update_migration_issue_status("issue-s-2", "completed")
        db.update_migration_issue_status("issue-s-3", "failed", error="err")

        summary = db.get_migration_summary("job-mig-1")
        assert summary["total"] == 6
        assert summary["pending"] == 2  # issue-s-4, issue-s-5
        assert summary["running"] == 1  # issue-s-0
        assert summary["completed"] == 2  # issue-s-1, issue-s-2
        assert summary["failed"] == 1  # issue-s-3


class TestGetRunningMigration:
    """Verify detection of an in-progress migration."""

    def test_returns_none_when_no_migration(self, db):
        result = db.get_running_migration("job-mig-1")
        assert result is None

    def test_returns_migration_id_when_running(self, db):
        db.create_migration_issue(
            issue_id="issue-r-1",
            job_id="job-mig-1",
            migration_id="mig-active",
            title="Active issue",
            severity="mandatory",
            effort="low",
            files=[],
            description="d",
            migration_hint="h",
        )
        db.update_migration_issue_status("issue-r-1", "running")
        result = db.get_running_migration("job-mig-1")
        assert result is not None
        assert result["migration_id"] == "mig-active"

    def test_returns_none_when_all_completed(self, db):
        db.create_migration_issue(
            issue_id="issue-r-2",
            job_id="job-mig-1",
            migration_id="mig-done",
            title="Done issue",
            severity="mandatory",
            effort="low",
            files=[],
            description="d",
            migration_hint="h",
        )
        db.update_migration_issue_status("issue-r-2", "completed")
        result = db.get_running_migration("job-mig-1")
        assert result is None
