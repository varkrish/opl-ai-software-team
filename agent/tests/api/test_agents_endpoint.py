"""
TDD: Tests for GET /api/jobs/<job_id>/agents endpoint.
Written BEFORE implementation (Red phase).
"""
import pytest
import json
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))


@pytest.fixture
def client():
    """Create test client for the LlamaIndex web app"""
    from llamaindex_crew.web.llamaindex_web_app import app, job_db
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client
    for job in job_db.get_all_jobs():
        job_db.delete_job(job['id'])


@pytest.fixture
def job_with_progress(client):
    """Create a job and simulate progress to a specific phase"""
    from llamaindex_crew.web.llamaindex_web_app import job_db

    # Create job via API
    response = client.post('/api/jobs', json={'vision': 'Test project'})
    data = json.loads(response.data)
    job_id = data['job_id']

    # Manually set job state to simulate progress (bypass actual workflow)
    # Phase names must match AGENT_DEFINITIONS: meta, product_owner, designer, tech_architect, development, frontend
    last_message = [
        {'timestamp': datetime.now().isoformat(), 'phase': 'meta', 'message': 'Project initialized successfully'},
        {'timestamp': datetime.now().isoformat(), 'phase': 'product_owner', 'message': 'User stories created'},
        {'timestamp': datetime.now().isoformat(), 'phase': 'designer', 'message': 'Design specs generated'},
        {'timestamp': datetime.now().isoformat(), 'phase': 'tech_architect', 'message': 'Generating SQL schema...'},
    ]
    job_db.update_job(job_id, {
        'status': 'running',
        'current_phase': 'tech_architect',
        'progress': 50,
        'last_message': json.dumps(last_message),
    })

    return job_id


def test_agents_endpoint_returns_200(client, job_with_progress):
    """Test that the agents endpoint returns 200 for a valid job"""
    response = client.get(f'/api/jobs/{job_with_progress}/agents')
    assert response.status_code == 200


def test_agents_endpoint_returns_6_agents(client, job_with_progress):
    """Test that the agents endpoint returns exactly 6 agents"""
    response = client.get(f'/api/jobs/{job_with_progress}/agents')
    data = json.loads(response.data)
    assert 'agents' in data
    assert len(data['agents']) == 6


def test_agents_endpoint_agent_fields(client, job_with_progress):
    """Test that each agent has the required fields"""
    response = client.get(f'/api/jobs/{job_with_progress}/agents')
    data = json.loads(response.data)

    required_fields = ['name', 'role', 'model', 'status', 'phase', 'last_activity', 'last_activity_at']
    for agent in data['agents']:
        for field in required_fields:
            assert field in agent, f"Missing field '{field}' in agent {agent.get('name', 'unknown')}"


def test_agents_status_derivation(client, job_with_progress):
    """Test that agent statuses are correctly derived from current_phase.
    Job is in 'tech_architect' phase, so:
    - meta, product_owner, designer -> completed
    - tech_architect -> working
    - development, frontend -> idle
    """
    response = client.get(f'/api/jobs/{job_with_progress}/agents')
    data = json.loads(response.data)

    agent_statuses = {a['phase']: a['status'] for a in data['agents']}

    assert agent_statuses['meta'] == 'completed'
    assert agent_statuses['product_owner'] == 'completed'
    assert agent_statuses['designer'] == 'completed'
    assert agent_statuses['tech_architect'] == 'working'
    assert agent_statuses['development'] == 'idle'
    assert agent_statuses['frontend'] == 'idle'


def test_agents_last_activity(client, job_with_progress):
    """Test that last_activity is populated from job messages"""
    response = client.get(f'/api/jobs/{job_with_progress}/agents')
    data = json.loads(response.data)

    meta_agent = next(a for a in data['agents'] if a['phase'] == 'meta')
    assert meta_agent['last_activity'] == 'Project initialized successfully'

    arch_agent = next(a for a in data['agents'] if a['phase'] == 'tech_architect')
    assert arch_agent['last_activity'] == 'Generating SQL schema...'


def test_agents_endpoint_job_not_found(client):
    """Test that the agents endpoint returns 404 for non-existent job"""
    response = client.get('/api/jobs/nonexistent-id/agents')
    assert response.status_code == 404


def test_agents_endpoint_idle_when_queued(client):
    """Test that all agents are idle when job is queued (not started)"""
    response = client.post('/api/jobs', json={'vision': 'Test project'})
    data = json.loads(response.data)
    job_id = data['job_id']

    response = client.get(f'/api/jobs/{job_id}/agents')
    data = json.loads(response.data)

    for agent in data['agents']:
        assert agent['status'] == 'idle', f"Agent {agent['name']} should be idle but is {agent['status']}"


def test_agents_all_completed_when_job_completed(client):
    """Test that all agents show completed when job is done"""
    from llamaindex_crew.web.llamaindex_web_app import job_db

    response = client.post('/api/jobs', json={'vision': 'Test project'})
    data = json.loads(response.data)
    job_id = data['job_id']

    job_db.update_job(job_id, {'status': 'completed', 'current_phase': 'completed', 'progress': 100})

    response = client.get(f'/api/jobs/{job_id}/agents')
    data = json.loads(response.data)

    for agent in data['agents']:
        assert agent['status'] == 'completed', f"Agent {agent['name']} should be completed but is {agent['status']}"
