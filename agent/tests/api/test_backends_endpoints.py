"""
API tests for backends endpoints
"""
import pytest
import json
import sys
from pathlib import Path

# Add paths for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'agent' / 'src'))

from crew_studio.llamaindex_web_app import app


@pytest.fixture
def client():
    """Create test client"""
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client


def test_list_backends(client):
    """Test GET /api/backends"""
    response = client.get('/api/backends')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert 'backends' in data
    assert isinstance(data['backends'], list)
    assert len(data['backends']) >= 1  # At least OPL AI Team
    
    # Check OPL backend is present
    opl = next((b for b in data['backends'] if b['name'] == 'opl-ai-team'), None)
    assert opl is not None
    assert opl['display_name'] == 'OPL AI Team'
    assert opl['available'] is True


def test_create_job_with_backend(client):
    """Test job creation with backend parameter"""
    response = client.post(
        '/api/jobs',
        json={'vision': 'Test project', 'backend': 'opl-ai-team'},
        content_type='application/json'
    )
    assert response.status_code == 201
    data = json.loads(response.data)
    assert 'job_id' in data
    assert data['status'] == 'queued'


def test_create_job_with_invalid_backend(client):
    """Test job creation with unknown backend"""
    response = client.post(
        '/api/jobs',
        json={'vision': 'Test project', 'backend': 'unknown-backend'},
        content_type='application/json'
    )
    assert response.status_code == 400
    data = json.loads(response.data)
    assert 'error' in data


def test_create_job_with_unavailable_backend(client):
    """Test job creation with unavailable backend (e.g. aider not installed)"""
    # Check if aider is in the backends list
    backends_resp = client.get('/api/backends')
    backends = json.loads(backends_resp.data)['backends']
    aider = next((b for b in backends if b['name'] == 'aider'), None)
    
    if aider and not aider['available']:
        response = client.post(
            '/api/jobs',
            json={'vision': 'Test project', 'backend': 'aider'},
            content_type='application/json'
        )
        assert response.status_code == 400
        data = json.loads(response.data)
        assert 'error' in data
