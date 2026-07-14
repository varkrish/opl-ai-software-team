import pytest
import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

# Insert src path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from llamaindex_crew.web.llamaindex_web_app import app, job_db

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    # Also disable at module level if imported
    try:
        from crew_studio import auth
        monkeypatch.setattr(auth, "AUTH_ENABLED", False)
    except ImportError:
        pass
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

def test_get_job_logs_endpoint_success(client, tmp_path):
    # Mock database get_job to return a job pointing to our temp workspace
    ws_dir = tmp_path / "workspace"
    ws_dir.mkdir()
    log_file = ws_dir / "execution.log"
    log_file.write_text("Hello Log Output", encoding="utf-8")
    
    mock_job = {
        "id": "test-job-id",
        "workspace_path": str(ws_dir),
        "status": "completed"
    }
    
    with patch.object(job_db, 'get_job', return_value=mock_job):
        response = client.get('/api/jobs/test-job-id/logs')
        assert response.status_code == 200
        assert response.data.decode('utf-8') == "Hello Log Output"
        assert response.headers['Content-Type'] == "text/plain; charset=utf-8"

def test_get_job_logs_endpoint_not_found(client):
    # Mock job database to return None
    with patch.object(job_db, 'get_job', return_value=None):
        response = client.get('/api/jobs/nonexistent-job-id/logs')
        assert response.status_code == 404

def test_get_job_logs_stream_endpoint_sse(client, tmp_path):
    ws_dir = tmp_path / "workspace"
    ws_dir.mkdir()
    log_file = ws_dir / "execution.log"
    log_file.write_text("Log Line 1\n", encoding="utf-8")
    
    mock_job = {
        "id": "test-job-id",
        "workspace_path": str(ws_dir),
        "status": "completed"
    }
    
    with patch.object(job_db, 'get_job', return_value=mock_job):
        # We perform a GET on stream endpoint. TBD: it returns a stream
        # To test generators in Flask client, we read response blocks.
        response = client.get('/api/jobs/test-job-id/logs/stream')
        assert response.status_code == 200
        assert response.headers['Content-Type'].startswith("text/event-stream")
        
        # Verify the SSE output matches expectations
        sse_data = response.data.decode('utf-8')
        assert "event: log" in sse_data
        assert "Log Line 1" in sse_data
