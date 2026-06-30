"""API tests for GET/POST /api/workflow/config."""
import json
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "agent" / "src"))


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_PATH", str(tmp_path / "workspace"))
    monkeypatch.setenv("JOB_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("CREW_TEST_NO_EXECUTOR", "1")
    monkeypatch.setenv("AUTH_ENABLED", "false")
    (tmp_path / "workspace").mkdir(exist_ok=True)

    from crew_studio import asgi_app as asgi_mod
    from crew_studio.asgi_app import app
    from crew_studio.job_database import JobDatabase

    asgi_mod.job_db = JobDatabase(tmp_path / "test.db")
    asgi_mod.base_workspace_path = tmp_path / "workspace"
    return TestClient(app)


class TestWorkflowConfig:
    def test_get_workflow_config_defaults(self, api_client):
        resp = api_client.get("/api/workflow/config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["configured"] is False
        assert data["plan_review_enabled"] is False
        assert data["solutioning_enabled"] is False
        assert data["auto_approve_plan"] is False

    def test_save_and_get_workflow_config(self, api_client):
        payload = {
            "plan_review_enabled": True,
            "solutioning_enabled": True,
            "solutioning_max_passes": 2,
            "solutioning_max_github_searches": 5,
            "auto_approve_plan": False,
        }
        save = api_client.post("/api/workflow/config", json=payload)
        assert save.status_code == 201
        assert save.json()["saved"] is True

        get = api_client.get("/api/workflow/config")
        assert get.status_code == 200
        data = get.json()
        assert data["configured"] is True
        assert data["plan_review_enabled"] is True
        assert data["solutioning_enabled"] is True
        assert data["solutioning_max_passes"] == 2
        assert data["solutioning_max_github_searches"] == 5

    def test_delete_workflow_config(self, api_client):
        api_client.post("/api/workflow/config", json={
            "plan_review_enabled": True,
            "solutioning_enabled": False,
            "solutioning_max_passes": 3,
            "solutioning_max_github_searches": 10,
            "auto_approve_plan": False,
        })
        del_resp = api_client.delete("/api/workflow/config")
        assert del_resp.status_code == 200
        get = api_client.get("/api/workflow/config")
        assert get.json()["configured"] is False

    def test_config_for_job_merges_owner_prefs(self, api_client, tmp_path, monkeypatch):
        from crew_studio import asgi_app as asgi_mod
        from crew_studio.workflow_config import merge_workflow_prefs_into_config
        from unittest.mock import MagicMock

        api_client.post("/api/workflow/config", json={
            "plan_review_enabled": True,
            "solutioning_enabled": True,
            "solutioning_max_passes": 2,
            "solutioning_max_github_searches": 8,
            "auto_approve_plan": False,
        })

        job_id = "job-wf-merge-test"
        ws = tempfile.mkdtemp()
        asgi_mod.job_db.create_job(job_id, "Test", ws, owner_id="mock-user-123")

        base = MagicMock()
        base.plan_review.enabled = False
        base.solutioning.enabled = False
        base.solutioning.max_passes = 3
        base.solutioning.max_github_searches = 10
        base.plan_review.model_copy = lambda update: MagicMock(enabled=update["enabled"])
        base.solutioning.model_copy = lambda update: MagicMock(**update)
        base.model_copy = lambda update: MagicMock(
            plan_review=update["plan_review"],
            solutioning=update["solutioning"],
        )

        prefs = asgi_mod.job_db.get_workflow_config("mock-user-123")
        merged = merge_workflow_prefs_into_config(base, prefs)
        assert merged.plan_review.enabled is True
        assert merged.solutioning.enabled is True
        assert merged.solutioning.max_passes == 2
