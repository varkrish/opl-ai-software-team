"""
TDD: Reject POST /api/jobs when LLM is not configured; expose GET /api/llm/status.
"""
from __future__ import annotations

import os
import sys

import httpx
import pytest


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


@pytest.fixture
def app(tmp_path, monkeypatch):
    """Fresh ASGI app with no server LLM key and no BYOK."""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("WORKSPACE_PATH", str(tmp_path / "workspace"))
    monkeypatch.setenv("JOB_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("MOUNT_FLASK_FALLBACK", "0")
    monkeypatch.setenv("SKIP_STARTUP_RESUME", "1")
    monkeypatch.setenv("CREW_TEST_NO_EXECUTOR", "1")
    monkeypatch.setenv("AUTH_ENABLED", "false")
    (tmp_path / "workspace").mkdir(exist_ok=True)

    for mod in list(sys.modules.keys()):
        if mod.startswith("crew_studio.asgi_app") or mod.startswith("crew_studio.auth"):
            del sys.modules[mod]

    from crew_studio.asgi_app import app as fastapi_app
    from crew_studio import asgi_app as asgi_mod
    from crew_studio.job_database import JobDatabase

    db = JobDatabase(tmp_path / "test.db")
    asgi_mod.job_db = db
    asgi_mod.base_workspace_path = tmp_path / "workspace"
    # Force empty server config (do not load ~/.crew-ai/config.yaml)
    monkeypatch.setattr(asgi_mod, "_get_config", lambda: None)

    return fastapi_app, db, asgi_mod


async def _client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )


@pytest.mark.asyncio
async def test_status_none_when_unconfigured(app):
    fastapi_app, _db, _ = app
    async with await _client(fastapi_app) as client:
        resp = await client.get("/api/llm/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is False
    assert body["source"] == "none"
    assert body.get("hint")


@pytest.mark.asyncio
async def test_create_job_rejected_without_llm(app):
    fastapi_app, db, _ = app
    async with await _client(fastapi_app) as client:
        resp = await client.post(
            "/api/jobs",
            json={"vision": "Build a calculator API"},
        )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "llm_not_configured"
    assert db.get_jobs_count() == 0


@pytest.mark.asyncio
async def test_create_job_allowed_with_byok(app):
    fastapi_app, db, _ = app
    db.save_llm_config(
        owner_id="mock-user-123",
        api_base_url="https://byok.example",
        api_key="sk-byok",
        model_manager="m",
        model_worker="w",
        model_reviewer="r",
    )
    async with await _client(fastapi_app) as client:
        status = await client.get("/api/llm/status")
        assert status.json()["configured"] is True
        assert status.json()["source"] == "byok"
        resp = await client.post(
            "/api/jobs",
            json={"vision": "Build a calculator API"},
        )
    assert resp.status_code == 201, resp.text
    assert "job_id" in resp.json()


@pytest.mark.asyncio
async def test_create_job_allowed_with_server_env_key(app, monkeypatch):
    fastapi_app, _db, _ = app
    monkeypatch.setenv("LLM_API_KEY", "sk-server-env")
    async with await _client(fastapi_app) as client:
        status = await client.get("/api/llm/status")
        assert status.json()["configured"] is True
        assert status.json()["source"] == "server"
        resp = await client.post(
            "/api/jobs",
            json={"vision": "Build with server key"},
        )
    assert resp.status_code == 201, resp.text
