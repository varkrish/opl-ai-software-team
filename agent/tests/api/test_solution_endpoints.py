"""API tests for solution review endpoints."""
import asyncio
import json
import sys
import uuid
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "agent" / "src"))


def _parse_meta(job):
    meta = job.get("metadata") or {}
    if isinstance(meta, str):
        return json.loads(meta) if meta else {}
    return meta if isinstance(meta, dict) else {}


async def _immediate_executor(executor, func, *args):
    return func()


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


def _seed_job(client_db_module, vision="Build app", status="pending_solution_review", metadata=None):
    job_id = str(uuid.uuid4())
    ws = tempfile.mkdtemp()
    client_db_module.create_job(job_id, vision, ws)
    updates = {"status": status, "current_phase": status}
    if metadata is not None:
        updates["metadata"] = json.dumps(metadata)
    client_db_module.update_job(job_id, updates)
    return job_id, ws


class TestGetSolution:
    def test_get_solution_returns_artifacts(self, api_client, tmp_path):
        from crew_studio import asgi_app as asgi_mod

        job_id, ws = _seed_job(asgi_mod.job_db)
        ws_path = Path(ws)
        ws_path.joinpath("solution_spec.md").write_text("# Spec", encoding="utf-8")
        ws_path.joinpath("solution_candidates.json").write_text("[{}]", encoding="utf-8")
        ws_path.joinpath("solution_critique_pass_1.json").write_text(
            '{"approved": true}', encoding="utf-8"
        )

        resp = api_client.get(f"/api/jobs/{job_id}/solution")
        assert resp.status_code == 200
        data = resp.json()
        assert "solution_spec.md" in data["artifacts"]
        assert data["solution_candidates"] == [{}]
        assert len(data["critique_history"]) == 1

    def test_get_solution_empty_workspace(self, api_client):
        from crew_studio import asgi_app as asgi_mod

        job_id, _ws = _seed_job(asgi_mod.job_db, status="running")
        resp = api_client.get(f"/api/jobs/{job_id}/solution")
        assert resp.status_code == 200
        data = resp.json()
        assert data["artifacts"] == {}
        assert data["solution_candidates"] == []
        assert data["critique_history"] == []

    def test_get_solution_auth_required(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKSPACE_PATH", str(tmp_path / "workspace"))
        monkeypatch.setenv("JOB_DB_PATH", str(tmp_path / "auth.db"))
        monkeypatch.setenv("AUTH_ENABLED", "true")
        (tmp_path / "workspace").mkdir(exist_ok=True)

        import crew_studio.auth as auth_mod
        auth_mod.AUTH_ENABLED = True

        from crew_studio import asgi_app as asgi_mod
        from crew_studio.asgi_app import app
        from crew_studio.job_database import JobDatabase

        asgi_mod.job_db = JobDatabase(tmp_path / "auth.db")
        job_id, _ = _seed_job(asgi_mod.job_db)

        with TestClient(app) as client:
            resp = client.get(f"/api/jobs/{job_id}/solution")
        assert resp.status_code == 401
        auth_mod.AUTH_ENABLED = False


class TestApproveSolutionGate:
    def test_approve_solution_gate(self, api_client):
        from crew_studio import asgi_app as asgi_mod

        job_id, _ = _seed_job(asgi_mod.job_db, status="pending_solution_review")
        with patch("crew_studio.asgi_app._dispatch_job"):
            resp = api_client.post(f"/api/jobs/{job_id}/approve")
        assert resp.status_code == 200
        job = asgi_mod.job_db.get_job(job_id)
        meta = _parse_meta(job)
        assert meta.get("solution_approved") is True
        assert job["status"] == "queued"
        assert job["current_phase"] == "product_owner"

    def test_approve_plan_gate_unchanged(self, api_client):
        from crew_studio import asgi_app as asgi_mod

        job_id, _ = _seed_job(asgi_mod.job_db, status="pending_review")
        with patch("crew_studio.asgi_app._dispatch_job"):
            resp = api_client.post(f"/api/jobs/{job_id}/approve")
        assert resp.status_code == 200
        job = asgi_mod.job_db.get_job(job_id)
        meta = _parse_meta(job)
        assert meta.get("pending_review_approved") is True
        assert job["current_phase"] == "development"

    def test_approve_rejects_wrong_status(self, api_client):
        from crew_studio import asgi_app as asgi_mod

        job_id, _ = _seed_job(asgi_mod.job_db, status="running")
        resp = api_client.post(f"/api/jobs/{job_id}/approve")
        assert resp.status_code == 400


class TestRefineSolution:
    def test_refine_solution_requires_feedback(self, api_client):
        from crew_studio import asgi_app as asgi_mod

        job_id, _ = _seed_job(asgi_mod.job_db, status="pending_solution_review")
        resp = api_client.post(f"/api/jobs/{job_id}/refine-solution", json={})
        assert resp.status_code == 422

    def test_refine_solution_updates_feedback_history(self, api_client):
        from crew_studio import asgi_app as asgi_mod

        job_id, ws = _seed_job(asgi_mod.job_db, status="pending_solution_review")
        Path(ws).joinpath("solution_candidates.json").write_text("[]", encoding="utf-8")

        def _fake_refine(*_args, **_kwargs):
            meta = _parse_meta(asgi_mod.job_db.get_job(job_id))
            history = meta.get("solution_feedback_history") or []
            history.append({"feedback": "add caching"})
            meta["solution_feedback_history"] = history
            asgi_mod.job_db.update_job(job_id, {
                "metadata": json.dumps(meta),
                "status": "pending_solution_review",
            })
            return {"status": "pending_solution_review", "feedback_rounds": 1}

        with patch("crew_studio.asgi_app.asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = _immediate_executor
            with patch(
                "src.llamaindex_crew.workflows.software_dev_workflow.SoftwareDevWorkflow.refine_solution",
                side_effect=_fake_refine,
            ):
                resp = api_client.post(
                    f"/api/jobs/{job_id}/refine-solution",
                    json={"feedback": "add caching"},
                )

        assert resp.status_code == 200
        meta = _parse_meta(asgi_mod.job_db.get_job(job_id))
        assert len(meta.get("solution_feedback_history", [])) == 1

    def test_refine_solution_rejects_wrong_status(self, api_client):
        from crew_studio import asgi_app as asgi_mod

        job_id, _ = _seed_job(asgi_mod.job_db, status="running")
        resp = api_client.post(
            f"/api/jobs/{job_id}/refine-solution",
            json={"feedback": "change approach"},
        )
        assert resp.status_code == 400

    def test_refine_solution_re_pauses(self, api_client):
        from crew_studio import asgi_app as asgi_mod

        job_id, ws = _seed_job(asgi_mod.job_db, status="pending_solution_review")
        Path(ws).joinpath("solution_candidates.json").write_text("[]", encoding="utf-8")

        def _fake_refine(*_args, **_kwargs):
            asgi_mod.job_db.update_job(job_id, {"status": "pending_solution_review"})
            return {"status": "pending_solution_review", "feedback_rounds": 1}

        with patch("crew_studio.asgi_app.asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = _immediate_executor
            with patch(
                "src.llamaindex_crew.workflows.software_dev_workflow.SoftwareDevWorkflow.refine_solution",
                side_effect=_fake_refine,
            ):
                resp = api_client.post(
                    f"/api/jobs/{job_id}/refine-solution",
                    json={"feedback": "use postgres"},
                )

        assert resp.status_code == 200
        assert resp.json().get("status") == "pending_solution_review"
        assert asgi_mod.job_db.get_job(job_id)["status"] == "pending_solution_review"
