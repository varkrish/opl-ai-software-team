"""
Unit tests for the Import mode bug fixes.

Regression coverage for two production failures in job 1b7f2a7c:

  Bug 1 — NameError: BaseLlamaIndexAgent not imported in meta_agent.py
    The triage agent instantiated BaseLlamaIndexAgent without importing it,
    crashing every job at the Meta phase.

  Bug 2 — sqlite3.ProgrammingError: type 'dict' is not supported
    asgi_app.py passed a raw Python dict as the SQLite metadata column value
    instead of json.dumps(). This crashed POST /api/jobs with mode=import.

These tests are intentionally lightweight — no LLM calls, no containers.
They should be fast enough to run on every commit.
"""

import json
import os
import sys
import uuid
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path setup — needed when running pytest from the repo root
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def _patch_paths():
    project_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), os.pardir, os.pardir)
    )
    for p in (
        project_root,
        os.path.join(project_root, "agent", "src"),
        os.path.join(project_root, "agent"),
    ):
        if p not in sys.path:
            sys.path.insert(0, p)


# ---------------------------------------------------------------------------
# Bug 1: meta_agent module-level import smoke tests
# ---------------------------------------------------------------------------

class TestMetaAgentImports:
    """
    Regression: BaseLlamaIndexAgent was used in meta_agent.py but never
    imported. Any job reaching the Meta phase raised NameError immediately.
    """

    def test_meta_agent_module_loads_without_error(self):
        """Importing the module must not raise NameError or ImportError."""
        # If the import fails the test fails here — no assertion needed.
        if "llamaindex_crew.agents.meta_agent" in sys.modules:
            del sys.modules["llamaindex_crew.agents.meta_agent"]
        import llamaindex_crew.agents.meta_agent  # noqa: F401

    def test_base_llamaindex_agent_accessible_from_meta_agent_module(self):
        """BaseLlamaIndexAgent must be importable from the agents package."""
        from llamaindex_crew.agents.base_agent import BaseLlamaIndexAgent  # noqa: F401
        assert BaseLlamaIndexAgent is not None

    def test_import_mode_recommended_error_exported(self):
        """ImportModeRecommendedError must be importable (used by workflow + web app)."""
        from llamaindex_crew.agents.meta_agent import ImportModeRecommendedError
        err = ImportModeRecommendedError("test", {"delivery_mode": "import_iterate"})
        assert str(err) == "test"
        assert err.triage["delivery_mode"] == "import_iterate"

    def test_heuristic_delivery_mode_import_prefix(self):
        """[Import] prefix (any case) at the start of vision triggers import_iterate."""
        from llamaindex_crew.agents.meta_agent import _heuristic_delivery_mode
        assert _heuristic_delivery_mode("[Import] fix the login page") == "import_iterate"
        assert _heuristic_delivery_mode("[IMPORT] update readme") == "import_iterate"
        assert _heuristic_delivery_mode("[import] lowercase also works") == "import_iterate"

    def test_heuristic_delivery_mode_greenfield(self):
        """A plain greenfield vision returns None (no heuristic match)."""
        from llamaindex_crew.agents.meta_agent import _heuristic_delivery_mode
        assert _heuristic_delivery_mode("Build a Frappe invoicing app") is None

    def test_heuristic_delivery_mode_import_prefix_not_in_middle(self):
        """[Import] in the middle of a string is NOT treated as import signal."""
        from llamaindex_crew.agents.meta_agent import _heuristic_delivery_mode
        result = _heuristic_delivery_mode("Build something cool [Import] some patterns")
        # Should not match — [Import] only triggers at the start
        assert result != "import_iterate"


# ---------------------------------------------------------------------------
# Bug 2: JobDatabase metadata must be JSON-serialised before update_job
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    """Fresh in-memory-style database for each test."""
    from crew_studio.job_database import JobDatabase
    return JobDatabase(tmp_path / "test_import_mode.db")


@pytest.fixture
def job_id(db):
    """Pre-created job ready for metadata tests."""
    jid = str(uuid.uuid4())
    db.create_job(jid, "import mode test", f"/tmp/job-{jid}")
    return jid


class TestMetadataJsonSerialization:
    """
    Regression: passing a raw Python dict to update_job raises
    sqlite3.ProgrammingError because SQLite cannot bind dicts.
    The fix is to json.dumps() before calling update_job.
    """

    def test_update_job_with_dict_raises(self, db, job_id):
        """Passing a raw dict to update_job must raise (documents the broken contract)."""
        import sqlite3
        with pytest.raises((sqlite3.ProgrammingError, sqlite3.InterfaceError)):
            db.update_job(job_id, {"metadata": {"job_mode": "import"}})

    def test_update_job_with_json_string_succeeds(self, db, job_id):
        """Passing json.dumps(dict) must succeed and be readable back."""
        meta = {"job_mode": "import"}
        result = db.update_job(job_id, {"metadata": json.dumps(meta)})
        assert result is True

    def test_metadata_round_trips_correctly(self, db, job_id):
        """Metadata stored as JSON string must round-trip through get_job."""
        meta = {"job_mode": "import", "source": "github"}
        db.update_job(job_id, {"metadata": json.dumps(meta)})
        job = db.get_job(job_id)
        assert job is not None
        stored = job.get("metadata")
        # The DB returns it as a string — callers must json.loads() it
        parsed = json.loads(stored) if isinstance(stored, str) else stored
        assert parsed["job_mode"] == "import"
        assert parsed["source"] == "github"

    def test_import_mode_sets_awaiting_import_phase(self, db, job_id):
        """After setting metadata, updating phase to awaiting_import must persist."""
        db.update_job(job_id, {"metadata": json.dumps({"job_mode": "import"})})
        db.update_job(job_id, {"status": "queued", "current_phase": "awaiting_import"})
        job = db.get_job(job_id)
        assert job["current_phase"] == "awaiting_import"
        assert job["status"] == "queued"
