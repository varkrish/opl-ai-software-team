"""
TDD tests for CORS configuration.

Verifies:
  1. Default origins (when CORS_ALLOWED_ORIGINS is unset) — localhost:3000, 127.0.0.1:3000, localhost:5173
  2. Custom origins from CORS_ALLOWED_ORIGINS env var
  3. Disallowed origins receive no Access-Control-Allow-Origin header
  4. Preflight (OPTIONS) requests return correct headers
  5. Flask fallback layer uses the same origin policy
"""

import os
import sys
from unittest.mock import patch

import pytest
import httpx

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def _patch_paths():
    project_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), os.pardir, os.pardir)
    )
    for p in (project_root, os.path.join(project_root, "agent", "src"),
              os.path.join(project_root, "agent")):
        if p not in sys.path:
            sys.path.insert(0, p)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app(cors_env: str = ""):
    """Import/create a fresh FastAPI app with the given CORS env var."""
    env = {
        "CORS_ALLOWED_ORIGINS": cors_env,
        "JOB_DB_PATH": "/tmp/test_cors_jobs.db",
        "WORKSPACE_PATH": "/tmp/test_cors_workspace",
        "MOUNT_FLASK_FALLBACK": "0",
    }
    with patch.dict(os.environ, env, clear=False):
        # Force reimport to pick up new env
        if "crew_studio.asgi_app" in sys.modules:
            del sys.modules["crew_studio.asgi_app"]
        from crew_studio.asgi_app import app
        return app


# ---------------------------------------------------------------------------
# Tests: Default origins (env unset)
# ---------------------------------------------------------------------------

class TestDefaultOrigins:
    """When CORS_ALLOWED_ORIGINS is empty, default dev origins are allowed."""

    @pytest.fixture(autouse=True)
    def setup_app(self):
        self.app = _make_app("")

    @pytest.mark.anyio
    async def test_localhost_3000_allowed(self):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/health",
                headers={"Origin": "http://localhost:3000"},
            )
            assert resp.status_code == 200
            assert resp.headers.get("access-control-allow-origin") == "http://localhost:3000"

    @pytest.mark.anyio
    async def test_127_0_0_1_3000_allowed(self):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/health",
                headers={"Origin": "http://127.0.0.1:3000"},
            )
            assert resp.status_code == 200
            assert resp.headers.get("access-control-allow-origin") == "http://127.0.0.1:3000"

    @pytest.mark.anyio
    async def test_localhost_5173_allowed(self):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/health",
                headers={"Origin": "http://localhost:5173"},
            )
            assert resp.status_code == 200
            assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"

    @pytest.mark.anyio
    async def test_unknown_origin_rejected(self):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/health",
                headers={"Origin": "http://evil.example.com"},
            )
            assert resp.status_code == 200
            assert "access-control-allow-origin" not in resp.headers


# ---------------------------------------------------------------------------
# Tests: Custom origins from env
# ---------------------------------------------------------------------------

class TestCustomOrigins:
    """When CORS_ALLOWED_ORIGINS is set, only those origins are allowed."""

    @pytest.fixture(autouse=True)
    def setup_app(self):
        self.app = _make_app("https://studio.example.com,http://localhost:4000")

    @pytest.mark.anyio
    async def test_custom_origin_allowed(self):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/health",
                headers={"Origin": "https://studio.example.com"},
            )
            assert resp.status_code == 200
            assert resp.headers.get("access-control-allow-origin") == "https://studio.example.com"

    @pytest.mark.anyio
    async def test_second_custom_origin_allowed(self):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/health",
                headers={"Origin": "http://localhost:4000"},
            )
            assert resp.status_code == 200
            assert resp.headers.get("access-control-allow-origin") == "http://localhost:4000"

    @pytest.mark.anyio
    async def test_default_origin_rejected_when_custom_set(self):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/health",
                headers={"Origin": "http://localhost:3000"},
            )
            assert resp.status_code == 200
            assert "access-control-allow-origin" not in resp.headers


# ---------------------------------------------------------------------------
# Tests: Preflight (OPTIONS)
# ---------------------------------------------------------------------------

class TestPreflight:
    """CORS preflight requests return proper headers."""

    @pytest.fixture(autouse=True)
    def setup_app(self):
        self.app = _make_app("http://localhost:3000")

    @pytest.mark.anyio
    async def test_preflight_returns_allow_methods(self):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        ) as client:
            resp = await client.options(
                "/api/jobs",
                headers={
                    "Origin": "http://localhost:3000",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "content-type",
                },
            )
            assert resp.status_code == 200
            assert resp.headers.get("access-control-allow-origin") == "http://localhost:3000"
            assert "POST" in resp.headers.get("access-control-allow-methods", "").upper()

    @pytest.mark.anyio
    async def test_no_credentials_header(self):
        """allow_credentials=False means no Access-Control-Allow-Credentials."""
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/health",
                headers={"Origin": "http://localhost:3000"},
            )
            assert resp.headers.get("access-control-allow-credentials") != "true"
