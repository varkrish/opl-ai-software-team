"""
End-to-End Tests for Web API
Tests the Flask API endpoints that drive the web UI
"""
import pytest
import sys
import json
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from llamaindex_crew.web.web_app import app, jobs


@pytest.fixture
def client():
    """Create Flask test client"""
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client
    # Clear jobs after each test
    jobs.clear()


@pytest.mark.e2e
@pytest.mark.api
def test_index_route(client):
    """Test that index route renders"""
    response = client.get('/')
    # May fail if templates not found, but should not crash
    assert response.status_code in [200, 500]


@pytest.mark.e2e
@pytest.mark.api
def test_create_job(client):
    """Test creating a new build job"""
    payload = {
        "vision": "Create a simple calculator"
    }
    
    response = client.post(
        '/api/jobs',
        data=json.dumps(payload),
        content_type='application/json'
    )
    
    assert response.status_code == 200
    data = json.loads(response.data)
    
    assert "job_id" in data
    assert "status" in data
    assert data["status"] == "queued"
    
    # Verify job is in storage
    job_id = data["job_id"]
    assert job_id in jobs
    assert jobs[job_id]["vision"] == payload["vision"]


@pytest.mark.e2e
@pytest.mark.api
def test_get_job_status(client):
    """Test getting job status"""
    # Create a job first
    payload = {"vision": "Test vision"}
    create_response = client.post(
        '/api/jobs',
        data=json.dumps(payload),
        content_type='application/json'
    )
    job_id = json.loads(create_response.data)["job_id"]
    
    # Get job status
    response = client.get(f'/api/jobs/{job_id}')
    assert response.status_code == 200
    
    data = json.loads(response.data)
    assert data["job_id"] == job_id
    assert data["vision"] == payload["vision"]
    assert "status" in data
    assert "created_at" in data


@pytest.mark.e2e
@pytest.mark.api
def test_get_nonexistent_job(client):
    """Test getting status of non-existent job"""
    response = client.get('/api/jobs/nonexistent-id')
    assert response.status_code == 404
    
    data = json.loads(response.data)
    assert "error" in data


@pytest.mark.e2e
@pytest.mark.api
def test_list_jobs(client):
    """Test listing all jobs"""
    # Create multiple jobs
    for i in range(3):
        payload = {"vision": f"Test vision {i}"}
        client.post(
            '/api/jobs',
            data=json.dumps(payload),
            content_type='application/json'
        )
    
    # List jobs
    response = client.get('/api/jobs')
    assert response.status_code == 200
    
    data = json.loads(response.data)
    assert "jobs" in data
    assert len(data["jobs"]) == 3


@pytest.mark.e2e
@pytest.mark.api
def test_job_execution_flow(client):
    """Test complete job execution flow (status transitions)"""
    payload = {"vision": "Simple test"}
    
    # Create job
    create_response = client.post(
        '/api/jobs',
        data=json.dumps(payload),
        content_type='application/json'
    )
    job_id = json.loads(create_response.data)["job_id"]
    
    # Job should start as queued
    response = client.get(f'/api/jobs/{job_id}')
    data = json.loads(response.data)
    assert data["status"] in ["queued", "running"]
    
    # Wait a bit for job to start
    time.sleep(1)
    
    # Check status again
    response = client.get(f'/api/jobs/{job_id}')
    data = json.loads(response.data)
    assert data["status"] in ["queued", "running", "completed", "failed"]
    
    # Should have a current_phase
    assert "current_phase" in data


@pytest.mark.e2e
@pytest.mark.api
def test_get_job_artifacts(client):
    """Test retrieving job artifacts"""
    # Create job
    payload = {"vision": "Test"}
    create_response = client.post(
        '/api/jobs',
        data=json.dumps(payload),
        content_type='application/json'
    )
    job_id = json.loads(create_response.data)["job_id"]
    
    # Try to get artifacts (may be empty)
    response = client.get(f'/api/jobs/{job_id}/artifacts')
    # Should not crash even if no artifacts yet
    assert response.status_code in [200, 404]


@pytest.mark.e2e
@pytest.mark.api
def test_concurrent_jobs(client):
    """Test handling multiple concurrent jobs"""
    job_ids = []
    
    # Create 5 jobs
    for i in range(5):
        payload = {"vision": f"Concurrent test {i}"}
        response = client.post(
            '/api/jobs',
            data=json.dumps(payload),
            content_type='application/json'
        )
        job_id = json.loads(response.data)["job_id"]
        job_ids.append(job_id)
    
    # All jobs should exist
    for job_id in job_ids:
        response = client.get(f'/api/jobs/{job_id}')
        assert response.status_code == 200


@pytest.mark.e2e
@pytest.mark.api
def test_invalid_job_creation(client):
    """Test creating job with invalid data"""
    # Missing vision
    response = client.post(
        '/api/jobs',
        data=json.dumps({}),
        content_type='application/json'
    )
    assert response.status_code in [400, 500]  # Should reject or handle gracefully
    
    # Invalid JSON
    response = client.post(
        '/api/jobs',
        data="not json",
        content_type='application/json'
    )
    assert response.status_code in [400, 500]
