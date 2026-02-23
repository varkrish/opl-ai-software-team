
import pytest
import json
import threading
from unittest.mock import MagicMock, patch
from flask import Flask
from crew_studio.refactor.blueprint import refactor_bp

@pytest.fixture
def mock_job_db():
    return MagicMock()

@pytest.fixture
def app(mock_job_db, tmp_path):
    app = Flask(__name__)
    app.config["JOB_DB"] = mock_job_db
    app.config["WORKSPACE_PATH"] = str(tmp_path)
    app.register_blueprint(refactor_bp)
    return app

@pytest.fixture
def client(app):
    return app.test_client()

@patch('crew_studio.refactor.blueprint.threading.Thread')
def test_start_refactor(mock_thread, client, mock_job_db, tmp_path):
    job_id = "test-job"
    
    # Setup job in DB
    mock_job_db.get_job.return_value = {
        "id": job_id,
        "status": "queued",
        "workspace_path": str(tmp_path / f"job-{job_id}")
    }
    
    # Create workspace dir
    (tmp_path / f"job-{job_id}").mkdir()

    # Call endpoint
    response = client.post(f"/api/jobs/{job_id}/refactor", json={
        "target_stack": "Java 17"
    })
    
    assert response.status_code == 202
    assert response.json["status"] == "started"
    
    # Verify Job DB update
    mock_job_db.update_job.assert_called_with(
        job_id, {"status": "running", "current_phase": "refactoring"}
    )
    
    # Verify thread started
    mock_thread.assert_called_once()
    mock_thread.return_value.start.assert_called_once()

@patch('crew_studio.refactor.blueprint.threading.Thread')
def test_start_refactor_with_preferences(mock_thread, client, mock_job_db, tmp_path):
    job_id = "test-job-prefs"
    
    # Setup job in DB
    mock_job_db.get_job.return_value = {
        "id": job_id,
        "status": "queued",
        "workspace_path": str(tmp_path / f"job-{job_id}")
    }
    
    # Create workspace dir
    (tmp_path / f"job-{job_id}").mkdir()

    # Call endpoint with tech_preferences
    response = client.post(f"/api/jobs/{job_id}/refactor", json={
        "target_stack": "Java 17",
        "tech_preferences": "Use Testcontainers"
    })
    
    assert response.status_code == 202
    
    # Verify thread started
    mock_thread.assert_called_once()
    
    # Verify that run_refactor_job was called with tech_preferences
    # Since run_refactor_job is imported inside the thread target, we need to inspect the target function
    # OR we can patch it if we know where it comes from.
    # However, since the blueprint imports it inside the function, we can't easily patch it from the test 
    # unless we patch 'crew_studio.refactor.blueprint.run_refactor_job' but it's not in that module scope?
    # Actually, sys.modules cache might allow patching 'crew_studio.refactor.runner.run_refactor_job'
    # BUT, let's look at the target function.
    
    target_func = mock_thread.call_args[1]['target']
    
    # We can try to execute the target function, but we need to mock run_refactor_job first
    # to catch the arguments.
    with patch('crew_studio.refactor.runner.run_refactor_job') as mock_runner:
        target_func()
        
        mock_runner.assert_called_once()
        _, kwargs = mock_runner.call_args
        assert kwargs['target_stack'] == "Java 17"
        assert kwargs['tech_preferences'] == "Use Testcontainers"

def test_start_refactor_job_not_found(client, mock_job_db):
    mock_job_db.get_job.return_value = None
    response = client.post("/api/jobs/missing-job/refactor", json={"target_stack": "Java 17"})
    assert response.status_code == 404

def test_get_refactor_plan_from_refactored_subdir(client, mock_job_db, tmp_path):
    """Plan is served from refactored/ subdir (primary location)."""
    job_id = "test-job"
    workspace = tmp_path / f"job-{job_id}"
    workspace.mkdir()
    (workspace / "refactored").mkdir()
    
    mock_job_db.get_job.return_value = {
        "id": job_id,
        "workspace_path": str(workspace)
    }
    
    # Create plan file under refactored/
    plan_data = {"tasks": [{"id": "1"}]}
    with open(workspace / "refactored" / "refactor_plan.json", "w") as f:
        json.dump(plan_data, f)
        
    response = client.get(f"/api/jobs/{job_id}/refactor/plan")
    
    assert response.status_code == 200
    assert response.json == plan_data


def test_get_refactor_plan_fallback_to_root(client, mock_job_db, tmp_path):
    """If refactored/ subdir has no plan, fall back to workspace root (backwards compat)."""
    job_id = "test-job-fb"
    workspace = tmp_path / f"job-{job_id}"
    workspace.mkdir()
    
    mock_job_db.get_job.return_value = {
        "id": job_id,
        "workspace_path": str(workspace)
    }
    
    # Create plan file at root (legacy location)
    plan_data = {"tasks": []}
    with open(workspace / "refactor_plan.json", "w") as f:
        json.dump(plan_data, f)
        
    response = client.get(f"/api/jobs/{job_id}/refactor/plan")
    
    assert response.status_code == 200
    assert response.json == plan_data


def test_get_refactor_plan_not_found(client, mock_job_db, tmp_path):
    job_id = "test-job"
    workspace = tmp_path / f"job-{job_id}"
    workspace.mkdir()
    
    mock_job_db.get_job.return_value = {
        "id": job_id,
        "workspace_path": str(workspace)
    }
    
    response = client.get(f"/api/jobs/{job_id}/refactor/plan")
    assert response.status_code == 404
