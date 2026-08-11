"""
A refinement records what the human asked but not what the agent did.

``complete_refinement`` stored only a status and a timestamp; the agent's actual
response existed solely as a ``logger.info`` line truncated to 500 characters,
and the chat bubbles in the Files workspace were fabricated client-side. So the
system held thousands of verbatim human instructions with no paired outcome —
half of every training example missing.

These tests pin the paired shape: instruction in, response and changed files
out, on both the success and failure paths, without breaking callers that
predate the columns.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from crew_studio.job_database import JobDatabase  # noqa: E402


@pytest.fixture()
def db(tmp_path):
    return JobDatabase(tmp_path / "test_jobs.db")


@pytest.fixture()
def job_id(db):
    jid = "job-1"
    db.create_job(jid, "Build a thing", str(Path("/tmp/ws")))
    return jid


def _refine(db, job_id, refinement_id="r-1", prompt="Fix the totals"):
    db.create_refinement(refinement_id, job_id, prompt)
    return refinement_id


class TestSchema:
    def test_refinements_table_has_response_columns(self, db):
        with db._get_conn() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(refinements)").fetchall()}
        assert "response" in cols
        assert "files_changed" in cols

    def test_existing_databases_are_migrated_in_place(self, tmp_path):
        # The project has no migration framework — schema evolves via idempotent
        # ALTER TABLE. Simulate a pre-existing DB without the new columns.
        path = tmp_path / "legacy.db"
        import sqlite3

        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE refinements (id TEXT PRIMARY KEY, job_id TEXT, prompt TEXT, "
            "file_path TEXT, status TEXT, created_at TEXT, completed_at TEXT, error TEXT)"
        )
        conn.commit()
        conn.close()

        JobDatabase(path)  # __init__ runs _init_schema

        conn = sqlite3.connect(path)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(refinements)").fetchall()}
        conn.close()
        assert {"response", "files_changed"} <= cols

    def test_reinitialising_is_idempotent(self, tmp_path):
        path = tmp_path / "twice.db"
        JobDatabase(path)
        JobDatabase(path)  # must not raise on duplicate ALTER


class TestCompleteRefinement:
    def test_response_is_persisted(self, db, job_id):
        rid = _refine(db, job_id)
        db.complete_refinement(rid, response="Added tax to InvoiceService.total()")

        row = db.get_refinement_history(job_id)[0]
        assert row["response"] == "Added tax to InvoiceService.total()"
        assert row["status"] == "completed"

    def test_files_changed_is_persisted_as_json(self, db, job_id):
        rid = _refine(db, job_id)
        db.complete_refinement(rid, response="done", files_changed=["a.py", "b.py"])

        row = db.get_refinement_history(job_id)[0]
        assert json.loads(row["files_changed"]) == ["a.py", "b.py"]

    def test_backwards_compatible_call_without_response(self, db, job_id):
        # Existing call sites pass only the id.
        rid = _refine(db, job_id)
        assert db.complete_refinement(rid) is True

        row = db.get_refinement_history(job_id)[0]
        assert row["status"] == "completed"
        assert not row["response"]

    def test_returns_false_for_unknown_refinement(self, db):
        assert db.complete_refinement("nope", response="x") is False

    def test_long_response_is_truncated_not_rejected(self, db, job_id):
        rid = _refine(db, job_id)
        db.complete_refinement(rid, response="z" * 100_000)

        row = db.get_refinement_history(job_id)[0]
        assert 0 < len(row["response"]) <= 10_000

    def test_non_string_response_is_coerced(self, db, job_id):
        # Agent responses arrive as whatever the LLM wrapper returned.
        rid = _refine(db, job_id)
        db.complete_refinement(rid, response={"text": "structured"})

        assert "structured" in db.get_refinement_history(job_id)[0]["response"]


class TestFailRefinement:
    def test_error_is_still_recorded(self, db, job_id):
        rid = _refine(db, job_id)
        db.fail_refinement(rid, "did not modify any files")

        row = db.get_refinement_history(job_id)[0]
        assert row["status"] == "failed"
        assert "did not modify" in row["error"]

    def test_failure_can_also_carry_a_response(self, db, job_id):
        rid = _refine(db, job_id)
        db.fail_refinement(rid, "no files changed", response="I could not find the module")

        row = db.get_refinement_history(job_id)[0]
        assert row["response"] == "I could not find the module"


class TestValidationIssueFixStrategy:
    """
    ``validation_issues`` declared a ``fix_strategy`` column the INSERT never
    wrote, so it was always NULL — the schema advertised a signal the code did
    not record.
    """

    def test_fix_strategy_is_persisted(self, db, job_id):
        db.create_validation_issue(
            "iss-1", job_id, "smoke_test", "error", "app.py", 10,
            "Container exited 1", fix_strategy="Add a health check before startup",
        )
        issue = db.get_validation_issues(job_id)[0]
        assert issue["fix_strategy"] == "Add a health check before startup"

    def test_fix_strategy_is_optional(self, db, job_id):
        db.create_validation_issue("iss-2", job_id, "lint", "warning", None, None, "Style")
        assert db.get_validation_issues(job_id)[0]["fix_strategy"] in (None, "")
