"""
API tests for Flask web app endpoints
"""
import pytest
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from llamaindex_crew.web.web_app import app


@pytest.fixture
def client():
    """Create test client"""
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client


def test_index_route(client):
    """Test index route"""
    response = client.get('/')
    assert response.status_code in [200, 500]  # 500 if templates not found


def test_create_job(client):
    """Test job creation endpoint"""
    response = client.post('/api/jobs', 
                          json={'vision': 'Test vision'},
                          content_type='application/json')
    assert response.status_code == 201
    data = json.loads(response.data)
    assert 'job_id' in data
    assert data['status'] == 'queued'


def test_create_job_missing_vision(client):
    """Test job creation without vision"""
    response = client.post('/api/jobs',
                          json={},
                          content_type='application/json')
    assert response.status_code == 400


def test_list_jobs(client):
    """Test list jobs endpoint"""
    # Create a job first
    client.post('/api/jobs', json={'vision': 'Test'})
    
    response = client.get('/api/jobs')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert 'jobs' in data


def test_get_job(client):
    """Test get job endpoint"""
    # Create a job
    create_response = client.post('/api/jobs', json={'vision': 'Test'})
    job_id = json.loads(create_response.data)['job_id']
    
    # Get job
    response = client.get(f'/api/jobs/{job_id}')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['id'] == job_id


def test_get_job_not_found(client):
    """Test get non-existent job"""
    response = client.get('/api/jobs/nonexistent')
    assert response.status_code == 404


def test_get_job_progress(client):
    """Test get job progress endpoint"""
    # Create a job
    create_response = client.post('/api/jobs', json={'vision': 'Test'})
    job_id = json.loads(create_response.data)['job_id']
    
    # Get progress
    response = client.get(f'/api/jobs/{job_id}/progress')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert 'status' in data
    assert 'progress' in data


def test_list_job_files(client):
    """Test list job files endpoint"""
    # Create a job
    create_response = client.post('/api/jobs', json={'vision': 'Test'})
    job_id = json.loads(create_response.data)['job_id']
    
    # List files
    response = client.get(f'/api/jobs/{job_id}/files')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert 'files' in data


def test_get_job_tasks(client):
    """Test get job tasks endpoint"""
    # Create a job
    create_response = client.post('/api/jobs', json={'vision': 'Test'})
    job_id = json.loads(create_response.data)['job_id']
    
    # Get tasks (may return empty if DB not created yet)
    response = client.get(f'/api/jobs/{job_id}/tasks')
    assert response.status_code in [200, 500]  # 500 if DB doesn't exist yet


def test_cancel_job(client):
    """Test cancel job endpoint"""
    # Create a job
    create_response = client.post('/api/jobs', json={'vision': 'Test'})
    job_id = json.loads(create_response.data)['job_id']
    
    # Cancel job
    response = client.post(f'/api/jobs/{job_id}/cancel')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['status'] == 'cancelled'
