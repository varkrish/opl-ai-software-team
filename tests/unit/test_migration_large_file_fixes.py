"""
TDD tests for migration large-file fixes.

Bug: Large .java files (>8K chars) are not being written properly during migration.
Root causes:
  1. _MAX_INLINE_CHARS too small (8K) — agent can't see full file
  2. Validation compares against truncated length, not actual file size
  3. "No changes" silently marks issues as completed
  4. max_tokens too low (2048) — agent can't output full file
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
# Fix 1: _MAX_INLINE_CHARS increased
# ═══════════════════════════════════════════════════════════════════════════════

class TestMaxInlineCharsIncreased:
    def test_max_inline_chars_is_at_least_50k(self):
        """_MAX_INLINE_CHARS must be >= 50,000 to handle large Java files."""
        from crew_studio.migration.runner import _MAX_INLINE_CHARS
        assert _MAX_INLINE_CHARS >= 50_000, (
            f"_MAX_INLINE_CHARS is {_MAX_INLINE_CHARS}, must be >= 50,000"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Fix 2: Validation uses actual file size
# ═══════════════════════════════════════════════════════════════════════════════

class TestValidationUsesActualFileSize:
    def test_validation_rejects_when_new_content_smaller_than_actual_original(self, tmp_path):
        """Validation should compare against actual file size, not truncated input.

        Scenario: A 50K-char file is truncated to _MAX_INLINE_CHARS for the prompt.
        The agent writes only 5K chars (a bad output). Validation must reject this
        because 5K < 30% of 50K (actual), even though 5K > 30% of _MAX_INLINE_CHARS.
        """
        job_db = _make_db(tmp_path)
        job_id = _create_job(job_db, "running", "migrating", "[MTA] test.zip")

        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "src").mkdir()
        java_file = ws / "src" / "BigService.java"

        # Original file: 50,000 chars
        original_content = "x" * 50_000
        java_file.write_text(original_content)

        issue_id = _add_issue(job_db, job_id, "pending", "Fix imports", files=["src/BigService.java"])

        from crew_studio.migration.runner import run_migration, _MAX_INLINE_CHARS

        def fake_agent_run(**kwargs):
            # Agent writes a truncated 5K-char file (bad output)
            java_file.write_text("y" * 5_000)

        with patch("llamaindex_crew.agents.migration_agent.MigrationExecutionAgent") as MockAgent, \
             patch("crew_studio.migration.runner.workspace_has_changes", return_value=True), \
             patch("crew_studio.migration.runner.git_snapshot"), \
             patch("crew_studio.migration.mta_parser.is_mta_issues_json", return_value=True), \
             patch("crew_studio.migration.mta_parser.parse_mta_issues_json", return_value=[
                 {"id": "rule-1", "title": "Fix imports", "severity": "mandatory",
                  "effort": "low", "files": ["src/BigService.java"],
                  "description": "Fix", "migration_hint": "Do it"}
             ]), \
             patch("crew_studio.migration.runner.load_migration_rules", return_value=None), \
             patch("crew_studio.migration.runner._expand_issues_to_workspace"):

            mock_instance = MockAgent.return_value
            mock_instance.run = MagicMock(side_effect=fake_agent_run)

            run_migration(
                job_id=job_id,
                workspace_path=str(ws),
                migration_goal="Migrate javax to jakarta",
                report_path="docs/issues.json",
                migration_notes=None,
                job_db=job_db,
            )

        # The issue must be marked as FAILED due to validation
        issues = job_db.get_migration_issues(job_id)
        statuses = {i["id"]: i["status"] for i in issues}
        # At least one issue should be failed (the one we created via parse_mta_issues_json)
        failed_count = sum(1 for s in statuses.values() if s == "failed")
        assert failed_count >= 1, f"Expected at least 1 failed issue, got statuses: {statuses}"


# ═══════════════════════════════════════════════════════════════════════════════
# Fix 3: "No changes" marks issues as failed
# ═══════════════════════════════════════════════════════════════════════════════

class TestNoChangesMarksIssuesFailed:
    def test_no_changes_after_retries_marks_failed(self, tmp_path):
        """When the agent makes no changes after all retries, issues must be marked failed."""
        job_db = _make_db(tmp_path)
        job_id = _create_job(job_db, "running", "migrating", "[MTA] test.zip")

        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "src").mkdir()
        java_file = ws / "src" / "Main.java"
        java_file.write_text("public class Main { /* javax.persistence */ }")

        issue_id = _add_issue(job_db, job_id, "pending", "Replace javax", files=["src/Main.java"])

        from crew_studio.migration.runner import run_migration

        with patch("llamaindex_crew.agents.migration_agent.MigrationExecutionAgent") as MockAgent, \
             patch("crew_studio.migration.runner.workspace_has_changes", return_value=False), \
             patch("crew_studio.migration.runner.git_snapshot"), \
             patch("crew_studio.migration.mta_parser.is_mta_issues_json", return_value=True), \
             patch("crew_studio.migration.mta_parser.parse_mta_issues_json", return_value=[
                 {"id": "rule-1", "title": "Replace javax", "severity": "mandatory",
                  "effort": "low", "files": ["src/Main.java"],
                  "description": "Fix", "migration_hint": "Do it"}
             ]), \
             patch("crew_studio.migration.runner.load_migration_rules", return_value=None), \
             patch("crew_studio.migration.runner._expand_issues_to_workspace"):

            mock_instance = MockAgent.return_value
            mock_instance.run = MagicMock()

            run_migration(
                job_id=job_id,
                workspace_path=str(ws),
                migration_goal="Migrate javax to jakarta",
                report_path="docs/issues.json",
                migration_notes=None,
                job_db=job_db,
            )

        # Issues must NOT be completed — they must be failed
        issues = job_db.get_migration_issues(job_id)
        statuses = {i["id"]: i["status"] for i in issues}
        failed_count = sum(1 for s in statuses.values() if s == "failed")
        completed_count = sum(1 for s in statuses.values() if s == "completed")
        assert failed_count >= 1, f"Expected failed issues, got: {statuses}"
        assert completed_count == 0, f"No-change issues should NOT be completed: {statuses}"


# ═══════════════════════════════════════════════════════════════════════════════
# Fix 4: MigrationExecutionAgent has higher max_tokens
# ═══════════════════════════════════════════════════════════════════════════════

class TestMigrationAgentMaxTokens:
    def test_migration_agent_uses_higher_max_tokens(self):
        """MigrationExecutionAgent must use max_tokens >= 4096 (capped to avoid provider 400)."""
        with patch("llamaindex_crew.agents.migration_agent.BaseLlamaIndexAgent") as MockBase:
            mock_instance = MagicMock()
            MockBase.return_value = mock_instance

            from llamaindex_crew.agents.migration_agent import MigrationExecutionAgent

            with patch("llamaindex_crew.agents.migration_agent.create_workspace_file_tools", return_value=[]), \
                 patch("llamaindex_crew.agents.migration_agent.load_prompt", return_value="backstory"), \
                 patch("llamaindex_crew.agents.migration_agent.get_llm_for_agent") as mock_get_llm:
                mock_llm = MagicMock()
                mock_llm.max_tokens = 2048
                mock_get_llm.return_value = mock_llm

                agent = MigrationExecutionAgent(Path("/tmp/ws"), "test-proj")

            call_kwargs = MockBase.call_args
            llm_arg = call_kwargs.kwargs.get("llm") or call_kwargs[1].get("llm")
            assert llm_arg is not None, "LLM must be explicitly passed to BaseLlamaIndexAgent"
            assert 4096 <= llm_arg.max_tokens <= 8192, (
                f"max_tokens is {llm_arg.max_tokens}, must be in [4096, 8192]"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# Fix 5: Truncation hint in prompt for large files
# ═══════════════════════════════════════════════════════════════════════════════

class TestTruncationHintInPrompt:
    def test_prompt_includes_truncation_warning_when_content_is_truncated(self):
        """When file content is truncated, the prompt must tell the agent to use file_reader."""
        from llamaindex_crew.agents.migration_agent import MigrationExecutionAgent

        with patch("llamaindex_crew.agents.migration_agent.BaseLlamaIndexAgent"), \
             patch("llamaindex_crew.agents.migration_agent.create_workspace_file_tools", return_value=[]), \
             patch("llamaindex_crew.agents.migration_agent.load_prompt", return_value="backstory"), \
             patch("llamaindex_crew.agents.migration_agent.get_llm_for_agent") as mock_get_llm:
            mock_llm = MagicMock()
            mock_llm.max_tokens = 2048
            mock_get_llm.return_value = mock_llm

            agent = MigrationExecutionAgent(Path("/tmp/ws"), "test-proj")

        prompt = agent.build_prompt(
            file_path="src/BigService.java",
            file_content="// truncated content...",
            issues=[{"id": "1", "title": "Fix imports", "migration_hint": "Do it"}],
            migration_goal="Migrate javax to jakarta",
            truncated=True,
        )

        assert "file_reader" in prompt.lower() or "file_reader" in prompt, (
            "Prompt must instruct agent to use file_reader when content is truncated"
        )
        assert "truncated" in prompt.lower(), (
            "Prompt must warn that content is truncated"
        )

    def test_prompt_has_no_truncation_warning_when_not_truncated(self):
        """When file is fully inlined, no truncation warning should appear."""
        from llamaindex_crew.agents.migration_agent import MigrationExecutionAgent

        with patch("llamaindex_crew.agents.migration_agent.BaseLlamaIndexAgent"), \
             patch("llamaindex_crew.agents.migration_agent.create_workspace_file_tools", return_value=[]), \
             patch("llamaindex_crew.agents.migration_agent.load_prompt", return_value="backstory"), \
             patch("llamaindex_crew.agents.migration_agent.get_llm_for_agent") as mock_get_llm:
            mock_llm = MagicMock()
            mock_llm.max_tokens = 2048
            mock_get_llm.return_value = mock_llm

            agent = MigrationExecutionAgent(Path("/tmp/ws"), "test-proj")

        prompt = agent.build_prompt(
            file_path="src/Small.java",
            file_content="public class Small {}",
            issues=[{"id": "1", "title": "Fix imports", "migration_hint": "Do it"}],
            migration_goal="Migrate javax to jakarta",
            truncated=False,
        )

        assert "truncated" not in prompt.lower(), (
            "No truncation warning when content is not truncated"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Fix 6: run_migration_retry has same fixes
# ═══════════════════════════════════════════════════════════════════════════════

class TestRetryValidationFixes:
    def test_retry_validation_uses_actual_file_size(self, tmp_path):
        """run_migration_retry validation must also use actual file size."""
        job_db = _make_db(tmp_path)
        job_id = _create_job(job_db, "failed", "error", "[MTA] test.zip")

        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "src").mkdir()
        java_file = ws / "src" / "Main.java"
        original_content = "x" * 50_000
        java_file.write_text(original_content)

        failed_id = _add_issue(job_db, job_id, "failed", "Fix imports", files=["src/Main.java"])

        from crew_studio.migration.runner import run_migration_retry

        def fake_agent_run(**kwargs):
            # Agent writes truncated output
            java_file.write_text("y" * 5_000)

        with patch("llamaindex_crew.agents.migration_agent.MigrationExecutionAgent") as MockAgent, \
             patch("crew_studio.migration.runner.workspace_has_changes", return_value=True), \
             patch("crew_studio.migration.runner.git_snapshot"), \
             patch("crew_studio.migration.runner.load_migration_rules", return_value=None):

            mock_instance = MockAgent.return_value
            mock_instance.run = MagicMock(side_effect=fake_agent_run)

            run_migration_retry(
                job_id=job_id,
                workspace_path=str(ws),
                migration_goal="Migrate javax to jakarta",
                job_db=job_db,
            )

        issues = {i["id"]: i for i in job_db.get_migration_issues(job_id)}
        assert issues[failed_id]["status"] == "failed", (
            f"Expected failed, got {issues[failed_id]['status']}"
        )

    def test_retry_no_changes_marks_failed(self, tmp_path):
        """run_migration_retry should fail issues when agent makes no changes."""
        job_db = _make_db(tmp_path)
        job_id = _create_job(job_db, "failed", "error", "[MTA] test.zip")

        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "src").mkdir()
        java_file = ws / "src" / "Main.java"
        java_file.write_text("public class Main {}")

        failed_id = _add_issue(job_db, job_id, "failed", "Fix imports", files=["src/Main.java"])

        from crew_studio.migration.runner import run_migration_retry

        with patch("llamaindex_crew.agents.migration_agent.MigrationExecutionAgent") as MockAgent, \
             patch("crew_studio.migration.runner.workspace_has_changes", return_value=False), \
             patch("crew_studio.migration.runner.git_snapshot"), \
             patch("crew_studio.migration.runner.load_migration_rules", return_value=None):

            mock_instance = MockAgent.return_value
            mock_instance.run = MagicMock()

            run_migration_retry(
                job_id=job_id,
                workspace_path=str(ws),
                migration_goal="Migrate javax to jakarta",
                job_db=job_db,
            )

        issues = {i["id"]: i for i in job_db.get_migration_issues(job_id)}
        assert issues[failed_id]["status"] == "failed", (
            f"Expected failed after no-changes, got {issues[failed_id]['status']}"
        )
