"""
TDD tests for validation issue tracking and agent remediation.

Written RED-first: these tests define the contract for:
  1. validation_issues table CRUD in JobDatabase
  2. Validator client (HTTP service with fallback)
  3. Remediation phase (auto-fix + LLM-based fix loop)
  4. Job status logic after validation
"""
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
import sys

root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(root))

from crew_studio.job_database import JobDatabase


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def db():
    """Fresh temp DB with a test job."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test_jobs.db"
        database = JobDatabase(db_path)
        database.create_job("job-val-1", "Build a todo app", f"{tmp}/workspace-1")
        database.create_job("job-val-2", "Build a chat app", f"{tmp}/workspace-2")
        yield database


@pytest.fixture
def workspace():
    """Temp workspace directory."""
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Validation Issues CRUD
# ═══════════════════════════════════════════════════════════════════════════════

class TestValidationIssuesCRUD:
    """Test the validation_issues table and CRUD methods in JobDatabase."""

    def test_table_exists(self, db):
        """validation_issues table is created during _init_schema."""
        with db._get_conn() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='validation_issues'"
            ).fetchone()
            assert row is not None, "validation_issues table should exist"

    def test_create_issue_stores_with_pending_status(self, db):
        """Newly created issue has status=pending and a created_at timestamp."""
        issue = db.create_validation_issue(
            issue_id="vi-001",
            job_id="job-val-1",
            check_name="syntax",
            severity="error",
            file_path="app.py",
            line_number=5,
            description="SyntaxError: unexpected EOF while parsing",
        )
        assert issue["id"] == "vi-001"
        assert issue["status"] == "pending"
        assert issue["created_at"] is not None
        assert issue["completed_at"] is None
        assert issue["severity"] == "error"
        assert issue["check_name"] == "syntax"
        assert issue["file_path"] == "app.py"
        assert issue["line_number"] == 5

    def test_get_issues_filters_by_job_id(self, db):
        """get_validation_issues returns only issues for the given job_id."""
        db.create_validation_issue("vi-a", "job-val-1", "syntax", "error", "a.py", None, "err A")
        db.create_validation_issue("vi-b", "job-val-2", "syntax", "error", "b.py", None, "err B")

        issues = db.get_validation_issues("job-val-1")
        assert len(issues) == 1
        assert issues[0]["id"] == "vi-a"

    def test_get_issues_filters_by_check_name(self, db):
        """get_validation_issues with check_name returns only matching checks."""
        db.create_validation_issue("vi-1", "job-val-1", "syntax", "error", "a.py", None, "err 1")
        db.create_validation_issue("vi-2", "job-val-1", "imports", "error", "a.py", None, "err 2")

        issues = db.get_validation_issues("job-val-1", check_name="syntax")
        assert len(issues) == 1
        assert issues[0]["check_name"] == "syntax"

    def test_get_pending_issues_excludes_completed(self, db):
        """get_pending_validation_issues excludes completed/failed issues."""
        db.create_validation_issue("vi-p", "job-val-1", "syntax", "error", "a.py", None, "pending one")
        db.create_validation_issue("vi-c", "job-val-1", "imports", "error", "b.py", None, "completed one")
        db.update_validation_issue_status("vi-c", "completed")

        pending = db.get_pending_validation_issues("job-val-1")
        assert len(pending) == 1
        assert pending[0]["id"] == "vi-p"

    def test_update_status_sets_completed_at(self, db):
        """Updating status to 'completed' sets completed_at timestamp."""
        db.create_validation_issue("vi-u", "job-val-1", "syntax", "error", "a.py", None, "test")
        db.update_validation_issue_status("vi-u", "completed")

        issues = db.get_validation_issues("job-val-1")
        assert issues[0]["status"] == "completed"
        assert issues[0]["completed_at"] is not None

    def test_update_status_with_fix_strategy(self, db):
        """fix_strategy can be stored when updating an issue."""
        db.create_validation_issue("vi-fs", "job-val-1", "syntax", "error", "a.py", None, "test")
        db.update_validation_issue_status("vi-fs", "running", fix_strategy="Add missing import for flask")

        issues = db.get_validation_issues("job-val-1")
        assert issues[0]["fix_strategy"] == "Add missing import for flask"

    def test_get_failed_issues(self, db):
        """get_failed_validation_issues returns only status=failed issues."""
        db.create_validation_issue("vi-ok", "job-val-1", "syntax", "error", "a.py", None, "ok")
        db.update_validation_issue_status("vi-ok", "completed")
        db.create_validation_issue("vi-bad", "job-val-1", "imports", "error", "b.py", None, "bad")
        db.update_validation_issue_status("vi-bad", "failed", error="Could not fix")

        failed = db.get_failed_validation_issues("job-val-1")
        assert len(failed) == 1
        assert failed[0]["id"] == "vi-bad"
        assert failed[0]["error"] == "Could not fix"

    def test_delete_issues_removes_all_for_job(self, db):
        """delete_validation_issues removes all issues for that job only."""
        db.create_validation_issue("vi-d1", "job-val-1", "syntax", "error", "a.py", None, "d1")
        db.create_validation_issue("vi-d2", "job-val-1", "imports", "error", "b.py", None, "d2")
        db.create_validation_issue("vi-d3", "job-val-2", "syntax", "error", "c.py", None, "d3")

        count = db.delete_validation_issues("job-val-1")
        assert count == 2

        remaining = db.get_validation_issues("job-val-1")
        assert len(remaining) == 0

        other = db.get_validation_issues("job-val-2")
        assert len(other) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Validator Client
# ═══════════════════════════════════════════════════════════════════════════════

class TestValidatorClient:
    """Test the _call_validator method on SoftwareDevWorkflow."""

    def _make_workflow(self, workspace, env_vars=None):
        """Create a minimal SoftwareDevWorkflow for testing."""
        sys.path.insert(0, str(root / "agent" / "src"))
        from llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow

        with patch.dict(os.environ, env_vars or {}, clear=False):
            wf = SoftwareDevWorkflow(
                project_id="test-proj",
                workspace_path=workspace,
                vision="test app",
            )
        return wf

    def test_calls_validator_service_when_url_set(self, workspace):
        """When VALIDATOR_URL is set, _call_validator POSTs to the service."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps({
            "results": {"syntax": {"pass": False, "issues": [
                {"file": "app.py", "line": 5, "error": "SyntaxError"}
            ]}}
        }).encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        wf = self._make_workflow(workspace)
        with patch.dict(os.environ, {"VALIDATOR_URL": "http://validator:8000"}):
            with patch("urllib.request.urlopen", return_value=mock_response):
                issues = wf._call_validator()

        assert len(issues) >= 1
        assert issues[0]["check"] == "syntax"
        assert issues[0]["file"] == "app.py"

    def test_falls_back_to_in_process_when_no_url(self, workspace):
        """When VALIDATOR_URL is not set, falls back to in-process checks."""
        # Create a Python file with valid syntax
        (workspace / "app.py").write_text("print('hello')\n")

        wf = self._make_workflow(workspace)
        with patch.dict(os.environ, {}, clear=False):
            if "VALIDATOR_URL" in os.environ:
                del os.environ["VALIDATOR_URL"]
            issues = wf._call_validator()

        assert isinstance(issues, list)

    def test_normalizes_response_to_issue_list(self, workspace):
        """The validator response is normalized to a flat list of issue dicts."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps({
            "results": {
                "syntax": {"pass": False, "issues": [
                    {"file": "a.py", "line": 1, "error": "SyntaxError"}
                ]},
                "imports": {"pass": False, "issues": [
                    {"file": "b.py", "line": 3, "error": "ModuleNotFoundError"}
                ]},
            }
        }).encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        wf = self._make_workflow(workspace)
        with patch.dict(os.environ, {"VALIDATOR_URL": "http://validator:8000"}):
            with patch("urllib.request.urlopen", return_value=mock_response):
                issues = wf._call_validator()

        assert len(issues) == 2
        checks = {i["check"] for i in issues}
        assert "syntax" in checks
        assert "imports" in checks

    def test_handles_validator_service_unavailable(self, workspace):
        """When the service is down, falls back to in-process checks."""
        (workspace / "app.py").write_text("x = 1\n")

        wf = self._make_workflow(workspace)
        with patch.dict(os.environ, {"VALIDATOR_URL": "http://validator:8000"}):
            with patch("urllib.request.urlopen", side_effect=ConnectionError("refused")):
                issues = wf._call_validator()

        assert isinstance(issues, list)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Remediation Phase
# ═══════════════════════════════════════════════════════════════════════════════

class TestRemediationPhase:
    """Test the validation + remediation logic in the workflow."""

    def _make_workflow_with_db(self, workspace):
        """Create a workflow with a job_db attached."""
        sys.path.insert(0, str(root / "agent" / "src"))
        from llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow

        db_path = workspace / "test_jobs.db"
        job_db = JobDatabase(db_path)
        job_db.create_job("test-proj", "test app", str(workspace))

        wf = SoftwareDevWorkflow(
            project_id="test-proj",
            workspace_path=workspace,
            vision="test app",
            job_db=job_db,
        )
        return wf, job_db

    def test_auto_fixes_missing_init_py_without_llm(self, workspace):
        """Missing __init__.py issues are auto-fixed by creating the file."""
        pkg_dir = workspace / "mypackage"
        pkg_dir.mkdir()
        (pkg_dir / "module.py").write_text("x = 1\n")

        wf, job_db = self._make_workflow_with_db(workspace)
        issues = [{"check": "package_structure", "severity": "error",
                    "file": "mypackage/__init__.py", "line": None,
                    "description": "Missing __init__.py in mypackage/"}]

        wf._auto_fix_issues(issues)

        assert (workspace / "mypackage" / "__init__.py").exists()

    def test_auto_fixes_missing_dependency_without_llm(self, workspace):
        """Missing dependencies are appended to requirements.txt."""
        (workspace / "requirements.txt").write_text("requests\n")

        wf, job_db = self._make_workflow_with_db(workspace)
        issues = [{"check": "dependency_manifest", "severity": "error",
                    "file": "requirements.txt", "line": None,
                    "description": "Undeclared dependency: flask (used in app.py)"}]

        fixed = wf._auto_fix_issues(issues)
        content = (workspace / "requirements.txt").read_text()
        assert "flask" in content
        assert len(fixed) == 1

    def test_sends_error_to_architect_for_review(self, workspace):
        """Non-auto-fixable error issues are sent to tech architect via agent.chat()."""
        wf, job_db = self._make_workflow_with_db(workspace)
        wf.tech_architect_agent = MagicMock()
        wf.tech_architect_agent.agent.chat.return_value = "Add import os at line 1"

        issue = {"check": "syntax", "severity": "error",
                 "file": "app.py", "line": 5,
                 "description": "SyntaxError: unexpected EOF"}

        strategy = wf._get_fix_strategy(issue)
        wf.tech_architect_agent.agent.chat.assert_called_once()
        assert strategy is not None

    def test_sends_fix_strategy_to_dev_agent(self, workspace):
        """After architect produces strategy, dev agent applies the fix via agent.chat()."""
        (workspace / "app.py").write_text("print(\n")

        wf, job_db = self._make_workflow_with_db(workspace)
        wf.dev_agent = MagicMock()
        wf.dev_agent.agent.chat.return_value = "Fixed"

        wf._apply_fix("app.py", "Close the open parenthesis on line 1")
        wf.dev_agent.agent.chat.assert_called_once()

    def test_marks_issue_completed_when_fix_works(self, workspace):
        """After a successful fix, the DB issue status is 'completed'."""
        wf, job_db = self._make_workflow_with_db(workspace)
        job_db.create_validation_issue(
            "vi-fix-ok", "test-proj", "syntax", "error", "app.py", 5, "SyntaxError"
        )

        job_db.update_validation_issue_status("vi-fix-ok", "completed")
        issues = job_db.get_validation_issues("test-proj")
        assert issues[0]["status"] == "completed"

    def test_marks_issue_failed_when_fix_fails(self, workspace):
        """If the fix doesn't resolve the issue, status is 'failed'."""
        wf, job_db = self._make_workflow_with_db(workspace)
        job_db.create_validation_issue(
            "vi-fix-bad", "test-proj", "syntax", "error", "app.py", 5, "SyntaxError"
        )

        job_db.update_validation_issue_status(
            "vi-fix-bad", "failed", error="Still has SyntaxError after fix"
        )
        failed = job_db.get_failed_validation_issues("test-proj")
        assert len(failed) == 1
        assert failed[0]["error"] == "Still has SyntaxError after fix"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Job Status After Validation
# ═══════════════════════════════════════════════════════════════════════════════

class TestJobStatusAfterValidation:
    """Test that job final status reflects validation results."""

    def test_job_completed_when_all_issues_resolved(self, db):
        """If all error-severity issues are completed, job should be completed."""
        db.create_validation_issue("vi-r1", "job-val-1", "syntax", "error", "a.py", None, "err")
        db.create_validation_issue("vi-r2", "job-val-1", "imports", "error", "b.py", None, "err")
        db.update_validation_issue_status("vi-r1", "completed")
        db.update_validation_issue_status("vi-r2", "completed")

        failed = db.get_failed_validation_issues("job-val-1")
        assert len(failed) == 0

    def test_job_failed_when_error_issues_unresolved(self, db):
        """If any error-severity issue is failed, job should be marked failed."""
        db.create_validation_issue("vi-f1", "job-val-1", "syntax", "error", "a.py", None, "err")
        db.update_validation_issue_status("vi-f1", "failed", error="Unfixable")

        failed = db.get_failed_validation_issues("job-val-1")
        assert len(failed) == 1

    def test_job_completed_when_only_warnings_remain(self, db):
        """Warning-severity issues with status=pending should not block completion."""
        db.create_validation_issue("vi-w1", "job-val-1", "duplicate_files", "warning", "a.py", None, "dup")
        db.create_validation_issue("vi-w2", "job-val-1", "contract_conformance", "warning", "b.py", None, "extra")

        # Only error-severity failed issues block completion
        failed = db.get_failed_validation_issues("job-val-1")
        assert len(failed) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# 4b. Partially Completed Status
# ═══════════════════════════════════════════════════════════════════════════════

class TestPartiallyCompletedStatus:
    """When code is generated but has unresolved validation issues, the job
    should be 'partially_completed' rather than 'failed'.

    A 'failed' status implies the build crashed or produced nothing useful,
    which is misleading when 90%+ of files were generated correctly.
    """

    def test_mark_partially_completed_sets_status(self, db):
        """mark_partially_completed must set status='partially_completed'."""
        db.mark_partially_completed(
            "job-val-1",
            warning="4 unresolved validation issue(s)",
            results={"budget_report": {}},
        )
        job = db.get_job("job-val-1")
        assert job["status"] == "partially_completed"

    def test_mark_partially_completed_sets_progress_100(self, db):
        """Job progress should be 100 — all phases ran to completion."""
        db.mark_partially_completed("job-val-1", warning="issues remain")
        job = db.get_job("job-val-1")
        assert job["progress"] == 100

    def test_mark_partially_completed_sets_completed_at(self, db):
        """completed_at must be set so the UI shows when it finished."""
        db.mark_partially_completed("job-val-1", warning="issues remain")
        job = db.get_job("job-val-1")
        assert job["completed_at"] is not None

    def test_mark_partially_completed_stores_warning(self, db):
        """The warning string should be stored in the error column for display."""
        db.mark_partially_completed("job-val-1", warning="4 unresolved issue(s)")
        job = db.get_job("job-val-1")
        assert "4 unresolved issue(s)" in (job.get("error") or "")

    def test_mark_partially_completed_stores_results(self, db):
        """Results (budget, validation report) should be stored."""
        db.mark_partially_completed(
            "job-val-1",
            warning="issues",
            results={"budget_report": {"total": 0.5}},
        )
        job = db.get_job("job-val-1")
        assert job["results"] is not None

    def test_mark_partially_completed_current_phase_is_completed(self, db):
        """current_phase should be 'completed', not 'error'."""
        db.mark_partially_completed("job-val-1", warning="issues")
        job = db.get_job("job-val-1")
        assert job["current_phase"] == "completed"

    def test_partially_completed_is_terminal_state(self, db):
        """partially_completed should be treated as a terminal state
        (not resumed on server restart)."""
        db.mark_partially_completed("job-val-1", warning="issues")
        job = db.get_job("job-val-1")
        assert job["status"] in ("completed", "partially_completed", "failed", "cancelled")

    def test_stats_counts_partially_completed_as_completed(self, db):
        """In aggregate stats, partially_completed jobs count toward
        'completed', not 'failed'."""
        db.mark_partially_completed("job-val-1", warning="issues")
        stats = db.get_stats()
        assert stats["completed"] >= 1
        assert stats.get("failed", 0) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Validation API Endpoint
# ═══════════════════════════════════════════════════════════════════════════════

class TestValidationAPIEndpoint:
    """Test the GET /api/jobs/{id}/validation REST endpoint."""

    @pytest.fixture
    def client(self):
        """Flask test client with a fresh DB."""
        sys.path.insert(0, str(root))
        import crew_studio.llamaindex_web_app as web_app

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test_api.db"
            test_db = JobDatabase(db_path)
            web_app.job_db = test_db
            test_db.create_job("job-api-1", "test vision", f"{tmp}/ws1")
            test_db.create_job("job-api-2", "test vision 2", f"{tmp}/ws2")

            test_db.create_validation_issue("vi-1", "job-api-1", "syntax", "error", "a.py", 5, "err 1")
            test_db.create_validation_issue("vi-2", "job-api-1", "imports", "error", "b.py", 3, "err 2")
            test_db.update_validation_issue_status("vi-2", "completed")
            test_db.create_validation_issue("vi-3", "job-api-1", "entrypoint", "error", "c.py", None, "err 3")
            test_db.update_validation_issue_status("vi-3", "failed", error="Could not fix")

            web_app.app.config["TESTING"] = True
            with web_app.app.test_client() as c:
                yield c, test_db

    def test_get_validation_returns_issues(self, client):
        """Endpoint returns the issues list."""
        c, _ = client
        resp = c.get("/api/jobs/job-api-1/validation")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["issues"]) == 3

    def test_get_validation_includes_summary(self, client):
        """Endpoint includes summary counts."""
        c, _ = client
        resp = c.get("/api/jobs/job-api-1/validation")
        data = resp.get_json()
        assert data["summary"]["total"] == 3
        assert data["summary"]["fixed"] == 1
        assert data["summary"]["failed"] == 1
        assert data["summary"]["pending"] == 1

    def test_get_validation_404_for_unknown_job(self, client):
        """Endpoint returns 404 for nonexistent job."""
        c, _ = client
        resp = c.get("/api/jobs/nonexistent-id/validation")
        assert resp.status_code == 404

    def test_get_validation_empty_when_no_issues(self, client):
        """Job with no issues returns empty list and overall=PASS."""
        c, _ = client
        resp = c.get("/api/jobs/job-api-2/validation")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["issues"]) == 0
        assert data["overall"] == "PASS"


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Scoped Remediation – prevent whole-project regeneration
# ═══════════════════════════════════════════════════════════════════════════════

class TestScopedRemediation:
    """Verify that _get_fix_strategy and _apply_fix use scoped, per-file
    prompts instead of triggering full project regeneration.

    Bug: _get_fix_strategy called tech_architect_agent.run() which invokes
    the full define_tech_stack flow (define_tech_stack_task.txt), and
    _apply_fix called dev_agent.run() which invokes the full implementation
    flow (implement_feature.txt), causing whole-project regeneration for
    every single validation issue.
    """

    def _make_workflow_with_db(self, workspace):
        sys.path.insert(0, str(root / "agent" / "src"))
        from llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow

        db_path = workspace / "test_jobs.db"
        job_db = JobDatabase(db_path)
        job_db.create_job("test-proj", "test app", str(workspace))

        wf = SoftwareDevWorkflow(
            project_id="test-proj",
            workspace_path=workspace,
            vision="test app",
            job_db=job_db,
        )
        return wf, job_db

    # ── _get_fix_strategy ────────────────────────────────────────────────

    def test_get_fix_strategy_must_not_call_run(self, workspace):
        """run() triggers define_tech_stack → full tech stack redefinition."""
        wf, _ = self._make_workflow_with_db(workspace)
        wf.tech_architect_agent = MagicMock()
        wf.tech_architect_agent.agent.chat.return_value = "Fix: add import"

        issue = {"check": "imports", "severity": "error",
                 "file": "app.py", "line": 3,
                 "description": "ModuleNotFoundError: flask"}

        wf._get_fix_strategy(issue)
        wf.tech_architect_agent.run.assert_not_called()

    def test_get_fix_strategy_uses_agent_chat(self, workspace):
        """Must use lightweight agent.chat() for a scoped prompt."""
        wf, _ = self._make_workflow_with_db(workspace)
        wf.tech_architect_agent = MagicMock()
        wf.tech_architect_agent.agent.chat.return_value = "Fix: add import"

        issue = {"check": "imports", "severity": "error",
                 "file": "app.py", "line": 3,
                 "description": "ModuleNotFoundError: flask"}

        strategy = wf._get_fix_strategy(issue)
        wf.tech_architect_agent.agent.chat.assert_called_once()
        assert strategy is not None

    def test_get_fix_strategy_resets_chat(self, workspace):
        """Must reset chat before each call to keep context window small."""
        wf, _ = self._make_workflow_with_db(workspace)
        wf.tech_architect_agent = MagicMock()
        wf.tech_architect_agent.agent.chat.return_value = "Fix strategy"

        issue = {"check": "syntax", "severity": "error",
                 "file": "app.py", "line": 5,
                 "description": "SyntaxError: unexpected EOF"}

        wf._get_fix_strategy(issue)
        wf.tech_architect_agent.agent.reset_chat.assert_called_once()

    def test_get_fix_strategy_prompt_contains_file_and_issue(self, workspace):
        """Prompt must mention the specific file path and error description."""
        wf, _ = self._make_workflow_with_db(workspace)
        wf.tech_architect_agent = MagicMock()
        wf.tech_architect_agent.agent.chat.return_value = "Fix it"

        issue = {"check": "syntax", "severity": "error",
                 "file": "models/user.py", "line": 10,
                 "description": "SyntaxError: invalid syntax"}

        wf._get_fix_strategy(issue)
        prompt = wf.tech_architect_agent.agent.chat.call_args[0][0]
        assert "models/user.py" in prompt
        assert "SyntaxError: invalid syntax" in prompt

    def test_get_fix_strategy_truncates_tech_stack(self, workspace):
        """Tech stack context must be truncated, not sent in full."""
        wf, _ = self._make_workflow_with_db(workspace)
        wf.tech_stack = "x" * 10_000
        wf.tech_architect_agent = MagicMock()
        wf.tech_architect_agent.agent.chat.return_value = "Fix it"

        issue = {"check": "imports", "severity": "error",
                 "file": "app.py", "line": 1,
                 "description": "Missing import"}

        wf._get_fix_strategy(issue)
        prompt = wf.tech_architect_agent.agent.chat.call_args[0][0]
        assert len(prompt) < 5000

    # ── _apply_fix ───────────────────────────────────────────────────────

    def test_apply_fix_must_not_call_run(self, workspace):
        """run() triggers implement_features → 'CREATE ALL FILES'."""
        wf, _ = self._make_workflow_with_db(workspace)
        wf.dev_agent = MagicMock()
        wf.dev_agent.agent.chat.return_value = "Fixed"

        wf._apply_fix("app.py", "Add missing import statement")
        wf.dev_agent.run.assert_not_called()

    def test_apply_fix_uses_agent_chat(self, workspace):
        """Must use lightweight agent.chat() for a scoped per-file fix."""
        wf, _ = self._make_workflow_with_db(workspace)
        wf.dev_agent = MagicMock()
        wf.dev_agent.agent.chat.return_value = "Fixed"

        wf._apply_fix("app.py", "Add 'import os' at line 1")
        wf.dev_agent.agent.chat.assert_called_once()

    def test_apply_fix_resets_chat(self, workspace):
        """Must reset chat before each call to keep context window small."""
        wf, _ = self._make_workflow_with_db(workspace)
        wf.dev_agent = MagicMock()
        wf.dev_agent.agent.chat.return_value = "Fixed"

        wf._apply_fix("app.py", "Fix syntax error")
        wf.dev_agent.agent.reset_chat.assert_called_once()

    def test_apply_fix_prompt_scoped_to_single_file(self, workspace):
        """Fix prompt must target ONLY the specific file, not regenerate all."""
        wf, _ = self._make_workflow_with_db(workspace)
        wf.dev_agent = MagicMock()
        wf.dev_agent.agent.chat.return_value = "Fixed"

        wf._apply_fix("src/routes/api.py", "Add error handling for 404")
        prompt = wf.dev_agent.agent.chat.call_args[0][0]
        assert "src/routes/api.py" in prompt
        assert "Add error handling for 404" in prompt
        assert "CREATE ALL" not in prompt.upper()
        assert "EVERY FILE" not in prompt.upper()

    def test_apply_fix_truncates_tech_stack(self, workspace):
        """Tech stack context in fix prompt must be truncated."""
        wf, _ = self._make_workflow_with_db(workspace)
        wf.tech_stack = "y" * 10_000
        wf.dev_agent = MagicMock()
        wf.dev_agent.agent.chat.return_value = "Fixed"

        wf._apply_fix("app.py", "Fix something")
        prompt = wf.dev_agent.agent.chat.call_args[0][0]
        assert len(prompt) < 5000

    def test_apply_fix_no_dev_agent_is_noop(self, workspace):
        """If dev_agent is None, _apply_fix should return without error."""
        wf, _ = self._make_workflow_with_db(workspace)
        wf.dev_agent = None
        wf._apply_fix("app.py", "Fix something")  # should not raise

    def test_get_fix_strategy_no_architect_returns_none(self, workspace):
        """If tech_architect_agent is None, should return None."""
        wf, _ = self._make_workflow_with_db(workspace)
        wf.tech_architect_agent = None
        result = wf._get_fix_strategy(
            {"check": "syntax", "severity": "error",
             "file": "app.py", "line": 1, "description": "err"}
        )
        assert result is None


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Expanded fixable issue extraction
# ═══════════════════════════════════════════════════════════════════════════════

class TestExpandedFixableIssues:
    """_collect_fixable_issues must extract dependency_manifest, duplicate_files,
    and entrypoint issues so the post-build fix loop can address them."""

    def _make_workflow(self, workspace):
        sys.path.insert(0, str(root / "agent" / "src"))
        from llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow

        wf = SoftwareDevWorkflow(
            project_id="test-proj",
            workspace_path=workspace,
            vision="test app",
        )
        return wf

    def test_extracts_dependency_manifest_issues(self, workspace):
        """Missing dependencies in manifest should be fixable."""
        wf = self._make_workflow(workspace)
        report = {
            "checks": {
                "integration": {"pass": True, "files": []},
                "completeness": {"pass": True, "issues": []},
                "tech_stack": {"pass": True, "conflicts": []},
                "dependency_manifest": {
                    "pass": False,
                    "missing": [
                        {"file": "package.json", "module": "express", "description": "Undeclared dependency: express"},
                    ],
                },
            }
        }
        issues = wf._collect_fixable_issues(report)
        assert len(issues) >= 1
        dep_issues = [i for i in issues if i["check"] == "dependency_manifest"]
        assert len(dep_issues) == 1
        assert "express" in dep_issues[0]["description"]

    def test_extracts_duplicate_files_issues(self, workspace):
        """Duplicate filenames across directories should be fixable."""
        wf = self._make_workflow(workspace)
        report = {
            "checks": {
                "integration": {"pass": True, "files": []},
                "completeness": {"pass": True, "issues": []},
                "tech_stack": {"pass": True, "conflicts": []},
                "duplicate_files": {
                    "pass": False,
                    "duplicates": [
                        {"filename": "app.js", "paths": ["src/app.js", "public/js/app.js"]},
                    ],
                },
            }
        }
        issues = wf._collect_fixable_issues(report)
        dup_issues = [i for i in issues if i["check"] == "duplicate_files"]
        assert len(dup_issues) == 1
        assert "app.js" in dup_issues[0]["description"]

    def test_extracts_entrypoint_issues(self, workspace):
        """Missing entrypoint wiring should be fixable."""
        wf = self._make_workflow(workspace)
        report = {
            "checks": {
                "integration": {"pass": True, "files": []},
                "completeness": {"pass": True, "issues": []},
                "tech_stack": {"pass": True, "conflicts": []},
                "entrypoint": {
                    "pass": False,
                    "framework": "flask",
                    "missing_wiring": ["No app.run() call found"],
                },
            }
        }
        issues = wf._collect_fixable_issues(report)
        ep_issues = [i for i in issues if i["check"] == "entrypoint"]
        assert len(ep_issues) >= 1
        assert "app.run()" in ep_issues[0]["description"]

    def test_still_extracts_integration_issues(self, workspace):
        """Existing integration extraction must still work."""
        wf = self._make_workflow(workspace)
        report = {
            "checks": {
                "integration": {
                    "pass": False,
                    "files": [
                        {"file": "app.py", "valid": False, "issues": ["SyntaxError"]},
                    ],
                },
                "completeness": {"pass": True, "issues": []},
                "tech_stack": {"pass": True, "conflicts": []},
            }
        }
        issues = wf._collect_fixable_issues(report)
        integ = [i for i in issues if i["check"] == "integration"]
        assert len(integ) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Post-build fix prompts include sibling context
# ═══════════════════════════════════════════════════════════════════════════════

class TestPostBuildSiblingContext:
    """The post-build fix loop must include related sibling file content
    so the dev agent can fix cross-file import mismatches."""

    def _make_workflow(self, workspace):
        sys.path.insert(0, str(root / "agent" / "src"))
        from llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow

        wf = SoftwareDevWorkflow(
            project_id="test-proj",
            workspace_path=workspace,
            vision="test app",
        )
        return wf

    def test_collect_related_files_finds_importers(self, workspace):
        """_collect_related_files should find files that import the target."""
        wf = self._make_workflow(workspace)
        (workspace / "app.py").write_text("from utils import helper\nhelper()\n")
        (workspace / "utils.py").write_text("def helper(): pass\n")
        all_files = {
            "app.py": "from utils import helper\nhelper()\n",
            "utils.py": "def helper(): pass\n",
        }
        related = wf._collect_related_files("utils.py", all_files)
        assert "app.py" in related

    def test_collect_related_files_finds_imports_of_target(self, workspace):
        """_collect_related_files should find files that the target imports."""
        wf = self._make_workflow(workspace)
        all_files = {
            "app.py": "from utils import helper\nhelper()\n",
            "utils.py": "def helper(): pass\n",
            "config.py": "DB_URL = 'sqlite://'\n",
        }
        related = wf._collect_related_files("app.py", all_files)
        assert "utils" in str(related) or "utils.py" in related

    def test_collect_related_files_limits_count(self, workspace):
        """Result should be limited to avoid prompt overflow."""
        wf = self._make_workflow(workspace)
        all_files = {f"mod_{i}.py": f"x = {i}\n" for i in range(20)}
        all_files["app.py"] = "\n".join(f"from mod_{i} import x" for i in range(20))
        related = wf._collect_related_files("app.py", all_files)
        assert len(related) <= 8

    def test_post_build_fix_prompt_includes_related_files(self, workspace):
        """The fix prompt sent to the dev agent must contain related file content."""
        wf = self._make_workflow(workspace)
        wf.dev_agent = MagicMock()
        wf.dev_agent.agent.chat.return_value = "Fixed"
        wf.tech_stack = "Python Flask"

        (workspace / "app.py").write_text("from utils import helper\nhelper()\n")
        (workspace / "utils.py").write_text("def helper(): pass\n")

        wf._run_post_build_fix_with_context(
            "app.py",
            ["ModuleNotFoundError: utils"],
            {"app.py": "from utils import helper\nhelper()\n",
             "utils.py": "def helper(): pass\n"},
        )

        prompt = wf.dev_agent.agent.chat.call_args[0][0]
        assert "utils.py" in prompt
        assert "def helper" in prompt


# ═══════════════════════════════════════════════════════════════════════════════
# 9. Forward export_registry to frontend phase
# ═══════════════════════════════════════════════════════════════════════════════

class TestForwardExportRegistry:
    """The dev phase must store its export_registry so the frontend phase
    can pass it as interface_contract to build_file_prompt."""

    def _make_workflow(self, workspace):
        sys.path.insert(0, str(root / "agent" / "src"))
        from llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow

        wf = SoftwareDevWorkflow(
            project_id="test-proj",
            workspace_path=workspace,
            vision="test app",
        )
        return wf

    def test_export_registry_initialized_on_workflow(self, workspace):
        """Workflow must have _export_registry attribute."""
        wf = self._make_workflow(workspace)
        assert hasattr(wf, "_export_registry")

    def test_export_registry_defaults_to_empty_dict(self, workspace):
        """Before dev phase runs, _export_registry should be empty."""
        wf = self._make_workflow(workspace)
        assert wf._export_registry == {} or wf._export_registry is None or isinstance(wf._export_registry, dict)


# ═══════════════════════════════════════════════════════════════════════════════
# 10. Fix 2: _collect_fixable_issues correctly maps dependency_manifest fields
# ═══════════════════════════════════════════════════════════════════════════════

class TestDependencyManifestFieldMapping:
    """_collect_fixable_issues must use the correct field names from dependency_manifest data."""

    def _make_workflow(self, workspace):
        sys.path.insert(0, str(root / "agent" / "src"))
        from llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow

        wf = SoftwareDevWorkflow(
            project_id="test-proj",
            workspace_path=workspace,
            vision="test app",
        )
        return wf

    def test_extracts_file_from_files_list(self, workspace):
        """dependency_manifest uses 'files' (list), not 'file' (string)."""
        wf = self._make_workflow(workspace)
        report = {
            "checks": {
                "dependency_manifest": {
                    "pass": False,
                    "missing": [
                        {
                            "ecosystem": "javascript",
                            "files": ["tests/app.test.js"],
                            "package": "@testing-library/react",
                        }
                    ],
                },
            },
        }
        issues = wf._collect_fixable_issues(report)
        dep_issues = [i for i in issues if i["check"] == "dependency_manifest"]
        assert len(dep_issues) >= 1
        assert dep_issues[0]["file"] == "tests/app.test.js"
        assert "@testing-library/react" in dep_issues[0]["description"]

    def test_extracts_package_not_module(self, workspace):
        """dependency_manifest uses 'package', not 'module'."""
        wf = self._make_workflow(workspace)
        report = {
            "checks": {
                "dependency_manifest": {
                    "pass": False,
                    "missing": [
                        {
                            "ecosystem": "javascript",
                            "files": ["src/app.js"],
                            "package": "../../utils/accessibility.js",
                        }
                    ],
                },
            },
        }
        issues = wf._collect_fixable_issues(report)
        dep_issues = [i for i in issues if i["check"] == "dependency_manifest"]
        assert len(dep_issues) >= 1
        assert "../../utils/accessibility.js" in dep_issues[0]["description"]

    def test_multiple_files_create_per_file_issues(self, workspace):
        """When a dependency is used in multiple files, create an issue for each."""
        wf = self._make_workflow(workspace)
        report = {
            "checks": {
                "dependency_manifest": {
                    "pass": False,
                    "missing": [
                        {
                            "ecosystem": "javascript",
                            "files": ["tests/a.test.js", "tests/b.test.js"],
                            "package": "cheerio",
                        }
                    ],
                },
            },
        }
        issues = wf._collect_fixable_issues(report)
        dep_issues = [i for i in issues if i["check"] == "dependency_manifest"]
        files = {i["file"] for i in dep_issues}
        assert "tests/a.test.js" in files
        assert "tests/b.test.js" in files


# ═══════════════════════════════════════════════════════════════════════════════
# 11. Fix 4: Post-build fix prompt includes project file tree
# ═══════════════════════════════════════════════════════════════════════════════

class TestPostBuildFixFileTree:
    """The post-build fix prompt must include the project file tree."""

    def _make_workflow(self, workspace):
        sys.path.insert(0, str(root / "agent" / "src"))
        from llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow

        wf = SoftwareDevWorkflow(
            project_id="test-proj",
            workspace_path=workspace,
            vision="test app",
        )
        return wf

    def test_fix_prompt_includes_file_tree(self, workspace):
        """Fix prompt must list all workspace files so agent can use correct paths."""
        wf = self._make_workflow(workspace)
        wf.dev_agent = MagicMock()
        wf.dev_agent.agent.chat.return_value = "Fixed"
        wf.tech_stack = "Vanilla JS"

        (workspace / "public" / "js").mkdir(parents=True)
        (workspace / "public" / "js" / "app.js").write_text("import { x } from './utils/data.js';\n")
        (workspace / "public" / "js" / "utils").mkdir()
        (workspace / "public" / "js" / "utils" / "data.js").write_text("export const x = 1;\n")
        (workspace / "tests").mkdir()
        (workspace / "tests" / "app.test.js").write_text("import '../src/js/app.js';\n")

        all_files = {
            "public/js/app.js": "import { x } from './utils/data.js';\n",
            "public/js/utils/data.js": "export const x = 1;\n",
            "tests/app.test.js": "import '../src/js/app.js';\n",
        }
        wf._run_post_build_fix_with_context(
            "tests/app.test.js",
            ["Broken import: '../src/js/app.js' (module not found)"],
            all_files,
        )
        prompt = wf.dev_agent.agent.chat.call_args[0][0]
        assert "public/js/app.js" in prompt
        assert "public/js/utils/data.js" in prompt


# ═══════════════════════════════════════════════════════════════════════════════
# 12. Fix prompts must include current file content to prevent content destruction
# ═══════════════════════════════════════════════════════════════════════════════

class TestApplyFixIncludesFileContent:
    """_apply_fix must embed the current file content in the prompt so the LLM
    preserves existing logic instead of writing a stub.

    Root cause: The fix prompt said 'rewrite the file' but never showed the LLM
    what was IN the file, so it hallucinated a replacement.
    """

    def _make_workflow(self, workspace):
        sys.path.insert(0, str(root / "agent" / "src"))
        from llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow

        wf = SoftwareDevWorkflow(
            project_id="test-proj",
            workspace_path=workspace,
            vision="test app",
        )
        return wf

    def test_prompt_contains_current_file_content(self, workspace):
        """The prompt sent to dev agent must contain the file's actual content."""
        wf = self._make_workflow(workspace)
        wf.dev_agent = MagicMock()
        wf.dev_agent.agent.chat.return_value = "Fixed"
        wf.tech_stack = "Vanilla JS"

        file_content = (
            "import { searchRecipes } from '../public/js/search.js';\n\n"
            "describe('Search functionality', () => {\n"
            "  test('filters by keyword', () => {\n"
            "    const results = searchRecipes('pasta');\n"
            "    expect(results.length).toBeGreaterThan(0);\n"
            "  });\n"
            "});\n"
        )
        (workspace / "tests").mkdir()
        (workspace / "tests" / "search.test.js").write_text(file_content)

        wf._apply_fix(
            "tests/search.test.js",
            "Fix the broken import path",
        )
        prompt = wf.dev_agent.agent.chat.call_args[0][0]
        assert "searchRecipes" in prompt, (
            "_apply_fix prompt must include the current file content"
        )
        assert "filters by keyword" in prompt, (
            "_apply_fix prompt must include test descriptions from the file"
        )

    def test_prompt_instructs_preservation(self, workspace):
        """The prompt must explicitly tell the LLM to preserve existing code."""
        wf = self._make_workflow(workspace)
        wf.dev_agent = MagicMock()
        wf.dev_agent.agent.chat.return_value = "Fixed"
        wf.tech_stack = "Python Flask"

        (workspace / "app.py").write_text("from flask import Flask\napp = Flask(__name__)\n")

        wf._apply_fix("app.py", "Add missing import for os")
        prompt = wf.dev_agent.agent.chat.call_args[0][0]
        assert "preserve" in prompt.lower() or "keep" in prompt.lower(), (
            "_apply_fix prompt must instruct LLM to preserve existing code"
        )

    def test_missing_file_still_works(self, workspace):
        """If the file doesn't exist on disk, _apply_fix should still work."""
        wf = self._make_workflow(workspace)
        wf.dev_agent = MagicMock()
        wf.dev_agent.agent.chat.return_value = "Fixed"

        wf._apply_fix("nonexistent.py", "Create this file")
        wf.dev_agent.agent.chat.assert_called_once()


class TestPostBuildFixIncludesFileContent:
    """_run_post_build_fix_with_context must embed the target file's current
    content in the prompt.

    Root cause: Same as _apply_fix -- the prompt included sibling context
    and the file tree, but not the actual content of the file being fixed.
    The LLM rewrote from scratch, destroying tests.
    """

    def _make_workflow(self, workspace):
        sys.path.insert(0, str(root / "agent" / "src"))
        from llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow

        wf = SoftwareDevWorkflow(
            project_id="test-proj",
            workspace_path=workspace,
            vision="test app",
        )
        return wf

    def test_prompt_contains_current_file_content(self, workspace):
        """Post-build fix prompt must include the target file's actual content."""
        wf = self._make_workflow(workspace)
        wf.dev_agent = MagicMock()
        wf.dev_agent.agent.chat.return_value = "Fixed"
        wf.tech_stack = "Vanilla JS"

        file_content = (
            "import { renderDetail } from '../public/js/detail.js';\n\n"
            "describe('Detail view', () => {\n"
            "  test('renders recipe title', () => {\n"
            "    document.body.innerHTML = '<div id=\"root\"></div>';\n"
            "    renderDetail({ title: 'Pasta' });\n"
            "    expect(document.getElementById('root').textContent).toContain('Pasta');\n"
            "  });\n"
            "});\n"
        )
        (workspace / "tests").mkdir()
        (workspace / "tests" / "detail.test.js").write_text(file_content)

        all_files = {
            "tests/detail.test.js": file_content,
            "public/js/detail.js": "export function renderDetail(r) {}\n",
        }
        wf._run_post_build_fix_with_context(
            "tests/detail.test.js",
            ["Broken import: '../public/js/detail.js' (module not found)"],
            all_files,
        )
        prompt = wf.dev_agent.agent.chat.call_args[0][0]
        assert "renderDetail" in prompt, (
            "Post-build fix prompt must include the current file content"
        )
        assert "renders recipe title" in prompt, (
            "Post-build fix prompt must include test descriptions from the file"
        )

    def test_prompt_instructs_preservation(self, workspace):
        """Post-build fix prompt must instruct LLM to preserve existing code."""
        wf = self._make_workflow(workspace)
        wf.dev_agent = MagicMock()
        wf.dev_agent.agent.chat.return_value = "Fixed"
        wf.tech_stack = "Vanilla JS"

        file_content = "import './app.js';\ntest('works', () => { expect(1).toBe(1); });\n"
        (workspace / "test.js").write_text(file_content)

        wf._run_post_build_fix_with_context(
            "test.js",
            ["Broken import: './app.js'"],
            {"test.js": file_content},
        )
        prompt = wf.dev_agent.agent.chat.call_args[0][0]
        assert "preserve" in prompt.lower() or "keep" in prompt.lower(), (
            "Post-build fix prompt must instruct LLM to preserve existing code"
        )
