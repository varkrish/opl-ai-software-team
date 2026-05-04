"""
Phase 0 — TDD RED → GREEN: Async API contract tests.

These tests verify the NON-BLOCKING behavior of the FastAPI ASGI app.
They use ``httpx.AsyncClient`` with ASGI transport to call the app
directly — no server process needed.

Contract:
  1. POST /api/jobs must return 201 within a strict time budget even when the
     job runner would take much longer.
  2. GET /health must remain responsive while a slow job is running.
  3. Multiple concurrent GET /api/jobs/{id} must all succeed without
     database lock errors.
"""

import asyncio
import os
import sys
import time
from unittest.mock import patch, MagicMock

import pytest
import httpx

# ---------------------------------------------------------------------------
# Timing thresholds — these define the async contract.
# ---------------------------------------------------------------------------
MAX_POST_LATENCY_SECS = 2.0
SLOW_JOB_DURATION_SECS = 10
MAX_HEALTH_LATENCY_SECS = 1.0
CONCURRENT_READS = 20


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def _patch_paths():
    """Ensure project root and agent/src are importable."""
    project_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), os.pardir, os.pardir)
    )
    for p in (project_root, os.path.join(project_root, "agent", "src"),
              os.path.join(project_root, "agent")):
        if p not in sys.path:
            sys.path.insert(0, p)


@pytest.fixture
def asgi_client(tmp_path, _patch_paths):
    """Synchronous fixture that creates an httpx AsyncClient backed by the ASGI app."""
    os.environ["WORKSPACE_PATH"] = str(tmp_path / "workspace")
    os.environ["JOB_DB_PATH"] = str(tmp_path / "test.db")
    (tmp_path / "workspace").mkdir(exist_ok=True)

    from crew_studio.asgi_app import app
    # Reset the DB to point at the temp path
    from crew_studio import asgi_app as asgi_mod
    from crew_studio.job_database import JobDatabase
    asgi_mod.job_db = JobDatabase(tmp_path / "test.db")
    asgi_mod.base_workspace_path = tmp_path / "workspace"

    return app


# ---------------------------------------------------------------------------
# Tests — all use asyncio
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_post_job_returns_within_time_budget(asgi_client):
    """POST /api/jobs must return 201 within MAX_POST_LATENCY_SECS."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=asgi_client),
        base_url="http://testserver",
    ) as client:
        t0 = time.monotonic()
        resp = await client.post(
            "/api/jobs",
            json={"vision": "TDD contract test — should return fast"},
        )
        elapsed = time.monotonic() - t0

    assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert "job_id" in body
    assert elapsed < MAX_POST_LATENCY_SECS, (
        f"POST /api/jobs took {elapsed:.2f}s — must be under {MAX_POST_LATENCY_SECS}s."
    )


@pytest.mark.asyncio
async def test_health_responds_while_job_runs(asgi_client):
    """GET /health must respond within MAX_HEALTH_LATENCY_SECS while a
    long-running job is in flight."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=asgi_client),
        base_url="http://testserver",
    ) as client:
        # Kick off a job (returns immediately)
        resp = await client.post(
            "/api/jobs",
            json={"vision": "TDD health check probe"},
        )
        assert resp.status_code == 201

        # Health check should still be fast
        t0 = time.monotonic()
        health_resp = await client.get("/health")
        elapsed = time.monotonic() - t0

    assert health_resp.status_code == 200
    assert elapsed < MAX_HEALTH_LATENCY_SECS, (
        f"GET /health took {elapsed:.2f}s during job execution — must be under "
        f"{MAX_HEALTH_LATENCY_SECS}s."
    )


@pytest.mark.asyncio
async def test_concurrent_job_reads(asgi_client):
    """Multiple concurrent GET /api/jobs/{id} must all succeed."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=asgi_client),
        base_url="http://testserver",
    ) as client:
        resp = await client.post(
            "/api/jobs",
            json={"vision": "Concurrent read test"},
        )
        assert resp.status_code == 201
        job_id = resp.json()["job_id"]

        errors: list = []
        latencies: list = []

        async def read_job():
            t0 = time.monotonic()
            try:
                r = await client.get(f"/api/jobs/{job_id}")
                latencies.append(time.monotonic() - t0)
                if r.status_code != 200:
                    errors.append(f"status={r.status_code}")
            except Exception as e:
                errors.append(str(e))

        await asyncio.gather(*(read_job() for _ in range(CONCURRENT_READS)))

    assert not errors, f"Concurrent reads had errors: {errors}"
    if latencies:
        p95 = sorted(latencies)[int(len(latencies) * 0.95)]
        assert p95 < MAX_HEALTH_LATENCY_SECS, (
            f"p95 read latency was {p95:.2f}s — must be under {MAX_HEALTH_LATENCY_SECS}s"
        )


# ---------------------------------------------------------------------------
# Flask regression test — ensures existing threaded dispatch still works
# ---------------------------------------------------------------------------

class TestFlaskJobDispatchReturnsQuickly:
    """The current Flask app already uses threading.Thread for job dispatch.
    This test ensures that behavior is preserved as a regression gate."""

    @pytest.fixture(autouse=True)
    def setup_client(self, tmp_path, _patch_paths):
        os.environ["WORKSPACE_PATH"] = str(tmp_path / "workspace")
        os.environ["JOB_DB_PATH"] = str(tmp_path / "test.db")
        (tmp_path / "workspace").mkdir(exist_ok=True)

        try:
            with patch("crew_studio.llamaindex_web_app.ConfigLoader") as mock_loader, \
                 patch("crew_studio.llamaindex_web_app.config", MagicMock()):
                mock_loader.load.return_value = MagicMock()
                import importlib
                import crew_studio.llamaindex_web_app as webapp
                importlib.reload(webapp)
                webapp.app.config["TESTING"] = True
                self.client = webapp.app.test_client()
                self.webapp = webapp
        except ImportError as e:
            pytest.skip(f"Flask app dependencies not available: {e}")

    @patch("crew_studio.llamaindex_web_app.threading.Thread")
    def test_flask_post_returns_201_immediately(self, mock_thread):
        """POST /api/jobs must return 201 and dispatch the job to a thread."""
        mock_thread_instance = MagicMock()
        mock_thread.return_value = mock_thread_instance

        t0 = time.monotonic()
        resp = self.client.post(
            "/api/jobs",
            json={"vision": "Flask regression — should return fast"},
        )
        elapsed = time.monotonic() - t0

        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.data}"
        assert elapsed < MAX_POST_LATENCY_SECS, (
            f"POST /api/jobs took {elapsed:.2f}s — it should return immediately "
            "since job is dispatched to a thread."
        )
        mock_thread_instance.start.assert_called_once()
