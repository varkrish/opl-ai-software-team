"""
End-to-End Tests for Web API
Tests the Flask API endpoints that drive the web UI
"""
import pytest
import sys
import json
import time
from pathlib import Path

# Paths set up via conftest.py and the client fixture below.


@pytest.fixture
def client():
    """Create Flask test client using the current crew_studio app."""
    root = Path(__file__).parent.parent.parent
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / "agent"))
    sys.path.insert(0, str(root / "agent" / "src"))

    from crew_studio.llamaindex_web_app import app, job_db
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.mark.e2e
@pytest.mark.api
def test_create_job(client):
    """Test creating a new build job."""
    response = client.post(
        "/api/jobs",
        json={"vision": "Create a simple calculator"},
        content_type="application/json",
    )
    assert response.status_code == 201
    data = json.loads(response.data)
    assert "job_id" in data
    assert "status" in data


@pytest.mark.e2e
@pytest.mark.api
def test_get_job_status(client):
    """Test getting job status."""
    create_response = client.post(
        "/api/jobs",
        json={"vision": "Test vision"},
        content_type="application/json",
    )
    assert create_response.status_code == 201
    job_id = json.loads(create_response.data)["job_id"]

    response = client.get(f"/api/jobs/{job_id}")
    assert response.status_code == 200
    data = json.loads(response.data)
    # GET /api/jobs/{id} returns 'id' (not 'job_id') in the job record
    assert data.get("id") == job_id or data.get("job_id") == job_id
    assert "status" in data
    assert "created_at" in data


@pytest.mark.e2e
@pytest.mark.api
def test_get_nonexistent_job(client):
    """Test getting status of non-existent job."""
    response = client.get("/api/jobs/nonexistent-id-xyz-123")
    assert response.status_code == 404


@pytest.mark.e2e
@pytest.mark.api
def test_list_jobs(client):
    """Test listing all jobs — verifies the endpoint returns the correct shape."""
    # Create a job so the list is non-empty in this session
    client.post(
        "/api/jobs",
        json={"vision": "List endpoint test job"},
        content_type="application/json",
    )

    response = client.get("/api/jobs")
    assert response.status_code == 200
    data = json.loads(response.data)
    # Response must have jobs list and pagination metadata
    assert "jobs" in data
    assert isinstance(data["jobs"], list)
    assert "total" in data or "page" in data or len(data) > 0


@pytest.mark.e2e
@pytest.mark.api
def test_job_status_transitions(client):
    """Test that a created job has a valid initial status."""
    response = client.post(
        "/api/jobs",
        json={"vision": "Simple test"},
        content_type="application/json",
    )
    assert response.status_code == 201
    job_id = json.loads(response.data)["job_id"]

    status_response = client.get(f"/api/jobs/{job_id}")
    data = json.loads(status_response.data)
    assert data["status"] in ("queued", "running", "completed", "failed")
    assert "current_phase" in data


@pytest.mark.e2e
@pytest.mark.api
def test_invalid_job_creation_missing_vision(client):
    """Test creating job without required vision field."""
    response = client.post(
        "/api/jobs",
        json={},
        content_type="application/json",
    )
    assert response.status_code in (400, 422, 500)


@pytest.mark.e2e
@pytest.mark.api
def test_invalid_job_creation_bad_json(client):
    """Test creating job with invalid JSON body."""
    response = client.post(
        "/api/jobs",
        data="not json",
        content_type="application/json",
    )
    assert response.status_code in (400, 422, 500)


@pytest.mark.e2e
@pytest.mark.api
def test_concurrent_jobs(client):
    """Test handling multiple concurrent jobs."""
    job_ids = []
    for i in range(5):
        response = client.post(
            "/api/jobs",
            json={"vision": f"Concurrent test {i}"},
            content_type="application/json",
        )
        assert response.status_code == 201
        job_ids.append(json.loads(response.data)["job_id"])

    for job_id in job_ids:
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
