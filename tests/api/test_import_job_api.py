"""
API-level regression tests for POST /api/jobs with mode=import.

Regression coverage for job 1b7f2a7c-8755-4e5c-93e4-e2f36a86eeaa which
failed with two cascading bugs:

  1. NameError: BaseLlamaIndexAgent not imported in meta_agent.py
  2. sqlite3.ProgrammingError: dict passed as SQLite metadata column value

These tests exercise the full FastAPI request → JobDatabase round-trip
without touching the LLM or spawning a background worker thread, so they
run fast and are safe for CI.
"""

import json
import os
import sys

import httpx
import pytest

# ---------------------------------------------------------------------------
# Path setup
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
# App fixture — isolated DB + workspace per test
# ---------------------------------------------------------------------------

@pytest.fixture
def app(tmp_path):
    """
    Create a fresh FastAPI app instance pointing at a temp DB and workspace.
    MOUNT_FLASK_FALLBACK=0 keeps the fixture fast (no Flask import).
    SKIP_STARTUP_RESUME=1 skips FastAPI lifespan job resume.
    CREW_TEST_NO_EXECUTOR=1 prevents greenfield POST /api/jobs from spawning the LLM pipeline.
    """
    os.environ["WORKSPACE_PATH"] = str(tmp_path / "workspace")
    os.environ["JOB_DB_PATH"] = str(tmp_path / "test.db")
    os.environ["MOUNT_FLASK_FALLBACK"] = "0"
    os.environ["SKIP_STARTUP_RESUME"] = "1"
    os.environ["CREW_TEST_NO_EXECUTOR"] = "1"
    (tmp_path / "workspace").mkdir(exist_ok=True)

    # Force re-import so env vars are picked up
    for mod in list(sys.modules.keys()):
        if mod.startswith("crew_studio.asgi_app"):
            del sys.modules[mod]

    from crew_studio.asgi_app import app as fastapi_app
    from crew_studio import asgi_app as asgi_mod
    from crew_studio.job_database import JobDatabase

    db = JobDatabase(tmp_path / "test.db")
    asgi_mod.job_db = db
    asgi_mod.base_workspace_path = tmp_path / "workspace"

    return fastapi_app, db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _post_job(client, payload: dict) -> httpx.Response:
    return await client.post("/api/jobs", json=payload)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCreateImportJob:
    """POST /api/jobs with mode=import must succeed and set the correct state."""

    @pytest.mark.asyncio
    async def test_returns_201(self, app):
        fastapi_app, _ = app
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=fastapi_app),
            base_url="http://testserver",
        ) as client:
            resp = await _post_job(client, {
                "vision": "[Import] fix the login page bug",
                "mode": "import",
            })
        assert resp.status_code == 201, resp.text

    @pytest.mark.asyncio
    async def test_response_contains_job_id(self, app):
        fastapi_app, _ = app
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=fastapi_app),
            base_url="http://testserver",
        ) as client:
            resp = await _post_job(client, {
                "vision": "[Import] update ARM64 Containerfile",
                "mode": "import",
            })
        body = resp.json()
        assert "job_id" in body
        assert len(body["job_id"]) == 36  # UUID format

    @pytest.mark.asyncio
    async def test_phase_is_awaiting_import(self, app):
        fastapi_app, db = app
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=fastapi_app),
            base_url="http://testserver",
        ) as client:
            resp = await _post_job(client, {
                "vision": "[Import] refactor registry.py",
                "mode": "import",
            })
        job_id = resp.json()["job_id"]
        job = db.get_job(job_id)
        assert job is not None
        assert job["current_phase"] == "awaiting_import"

    @pytest.mark.asyncio
    async def test_status_is_queued(self, app):
        fastapi_app, db = app
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=fastapi_app),
            base_url="http://testserver",
        ) as client:
            resp = await _post_job(client, {
                "vision": "[Import] add ARM64 support",
                "mode": "import",
            })
        job_id = resp.json()["job_id"]
        job = db.get_job(job_id)
        assert job["status"] == "queued"

    @pytest.mark.asyncio
    async def test_metadata_job_mode_stored_correctly(self, app):
        """
        Regression for Bug 2: metadata must be stored as a JSON string in SQLite
        (not a raw dict), and get_job must return it as a parsed dict with job_mode=import.
        """
        fastapi_app, db = app
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=fastapi_app),
            base_url="http://testserver",
        ) as client:
            resp = await _post_job(client, {
                "vision": "[Import] update readme",
                "mode": "import",
            })
        job_id = resp.json()["job_id"]

        # get_job auto-parses the JSON string column back to a dict — check content
        job = db.get_job(job_id)
        meta = job.get("metadata")
        assert isinstance(meta, dict), f"get_job should return metadata as dict, got {type(meta)}"
        assert meta.get("job_mode") == "import"

        # Also verify the raw SQLite column is actually a string (not a dict),
        # confirming the fix (json.dumps) is in place in asgi_app.py
        import sqlite3
        with sqlite3.connect(str(db.db_path)) as conn:
            row = conn.execute(
                "SELECT metadata FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        assert row is not None
        raw = row[0]
        assert isinstance(raw, str), f"Raw SQLite column must be a JSON string, got {type(raw)}: {raw!r}"
        assert json.loads(raw).get("job_mode") == "import"

    @pytest.mark.asyncio
    async def test_metadata_extra_fields_preserved(self, app):
        """Extra metadata fields in the request body must survive the round-trip."""
        fastapi_app, db = app
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=fastapi_app),
            base_url="http://testserver",
        ) as client:
            resp = await _post_job(client, {
                "vision": "[Import] update services",
                "mode": "import",
                "metadata": {"source": "github", "repo": "vyogotech/frappista"},
            })
        job_id = resp.json()["job_id"]
        job = db.get_job(job_id)
        meta = job["metadata"]
        assert isinstance(meta, dict)
        assert meta["job_mode"] == "import"
        assert meta["source"] == "github"
        assert meta["repo"] == "vyogotech/frappista"


class TestCreateGreenfieldJob:
    """Existing greenfield flow must be unaffected by the import mode changes."""

    @pytest.mark.asyncio
    async def test_greenfield_returns_201(self, app):
        fastapi_app, _ = app
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=fastapi_app),
            base_url="http://testserver",
        ) as client:
            resp = await _post_job(client, {"vision": "Build a Frappe invoicing app"})
        assert resp.status_code == 201, resp.text

    @pytest.mark.asyncio
    async def test_greenfield_phase_is_not_awaiting_import(self, app):
        fastapi_app, db = app
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=fastapi_app),
            base_url="http://testserver",
        ) as client:
            resp = await _post_job(client, {"vision": "Build a React dashboard"})
        job_id = resp.json()["job_id"]
        job = db.get_job(job_id)
        assert job["current_phase"] != "awaiting_import"


class TestCreateJobValidation:
    """Input validation — missing or empty vision must be rejected."""

    @pytest.mark.asyncio
    async def test_missing_vision_returns_422(self, app):
        fastapi_app, _ = app
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=fastapi_app),
            base_url="http://testserver",
        ) as client:
            resp = await client.post("/api/jobs", json={"mode": "import"})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_empty_vision_returns_400(self, app):
        fastapi_app, _ = app
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=fastapi_app),
            base_url="http://testserver",
        ) as client:
            resp = await _post_job(client, {"vision": "", "mode": "import"})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_invalid_json_returns_400(self, app):
        fastapi_app, _ = app
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=fastapi_app),
            base_url="http://testserver",
        ) as client:
            resp = await client.post(
                "/api/jobs",
                content=b"not json",
                headers={"Content-Type": "application/json"},
            )
        assert resp.status_code == 400
