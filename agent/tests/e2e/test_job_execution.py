"""
End-to-end tests for job execution lifecycle.

These tests verify the complete job flow from creation through execution.
REGRESSION: These tests would have caught the duplicate run_job_async bug.
"""
import pytest
import time
import json
from pathlib import Path


def test_job_starts_within_timeout(client):
    """
    CRITICAL REGRESSION TEST
    
    Verifies that a created job actually starts executing within a reasonable time.
    This test would have caught the duplicate run_job_async function bug where
    jobs were created but never started.
    """
    # Create a simple job
    response = client.post(
        '/api/jobs',
        json={'vision': 'create a hello world program', 'backend': 'opl-ai-team'},
        content_type='application/json'
    )
    
    assert response.status_code == 201
    job_id = json.loads(response.data)['job_id']
    
    # Wait up to 10 seconds for job to start
    max_wait = 10
    start_time = time.time()
    job_started = False
    
    while time.time() - start_time < max_wait:
        response = client.get(f'/api/jobs/{job_id}')
        assert response.status_code == 200
        job = json.loads(response.data)
        
        if job.get('started_at') is not None:
            job_started = True
            break
        
        time.sleep(0.5)
    
    # ASSERTION: Job must have started
    assert job_started, (
        f"Job {job_id} did not start within {max_wait} seconds. "
        f"Status: {job.get('status')}, Started: {job.get('started_at')}"
    )
    
    # Additional verification
    assert job['status'] in ['running', 'completed'], \
        f"Job status is '{job['status']}', expected 'running' or 'completed'"


def test_job_progress_updates(client):
    """
    Verify that job progress is actually reported back to the database.
    
    This ensures the workflow -> database communication is working.
    """
    response = client.post(
        '/api/jobs',
        json={'vision': 'simple counter', 'backend': 'opl-ai-team'},
        content_type='application/json'
    )
    
    job_id = json.loads(response.data)['job_id']
    
    # Wait for job to start
    time.sleep(5)
    
    # Check progress endpoint
    response = client.get(f'/api/jobs/{job_id}/progress')
    assert response.status_code == 200
    progress = json.loads(response.data)
    
    # Job should have started and show some progress or phase change
    assert progress['status'] != 'queued', \
        "Job is still queued after 5 seconds - workflow not starting"
    
    # At minimum, phase should have changed from 'queued'
    assert progress['current_phase'] != 'queued', \
        f"Job phase is still 'queued' after 5 seconds: {progress}"


def test_agents_show_activity(client):
    """
    Verify that agent status is updated when job runs.
    
    This ensures the UI will show agent activity.
    """
    response = client.post(
        '/api/jobs',
        json={'vision': 'test app', 'backend': 'opl-ai-team'},
        content_type='application/json'
    )
    
    job_id = json.loads(response.data)['job_id']
    
    # Wait for agents to start working
    time.sleep(8)
    
    response = client.get(f'/api/jobs/{job_id}/agents')
    assert response.status_code == 200
    agents = json.loads(response.data)['agents']
    
    # At least one agent should show activity
    active_agents = [a for a in agents if a['status'] != 'idle']
    assert len(active_agents) > 0, \
        "No agents showing activity after 8 seconds - workflow may not be running"


def test_tasks_are_created(client):
    """
    Verify that tasks database is populated.
    
    This ensures the Kanban board will have data to display.
    """
    response = client.post(
        '/api/jobs',
        json={'vision': 'calculator', 'backend': 'opl-ai-team'},
        content_type='application/json'
    )
    
    job_id = json.loads(response.data)['job_id']
    
    # Wait for workflow initialization
    time.sleep(5)
    
    response = client.get(f'/api/jobs/{job_id}/tasks')
    assert response.status_code == 200
    tasks = json.loads(response.data)['tasks']
    
    # Tasks should be created for the workflow phases
    assert len(tasks) > 0, \
        "No tasks created after 5 seconds - workflow not initializing properly"
    
    # Verify task structure
    assert all('phase' in t and 'agent' in t and 'status' in t for t in tasks), \
        "Tasks missing required fields"


def test_workflow_creates_files(client, tmp_path):
    """
    Verify that the workflow actually generates output files.
    
    This is the ultimate test - did it do what it's supposed to do?
    """
    response = client.post(
        '/api/jobs',
        json={'vision': 'hello world', 'backend': 'opl-ai-team'},
        content_type='application/json'
    )
    
    job_id = json.loads(response.data)['job_id']
    
    # Get job details to find workspace
    response = client.get(f'/api/jobs/{job_id}')
    job = json.loads(response.data)
    workspace_path = Path(job['workspace_path'])
    
    # Wait for workflow to generate some files
    max_wait = 30
    start_time = time.time()
    files_created = False
    
    while time.time() - start_time < max_wait:
        if workspace_path.exists():
            files = list(workspace_path.rglob('*'))
            # Exclude just the database files
            code_files = [f for f in files if f.is_file() and not f.name.endswith('.db') and not f.name.endswith('.json')]
            if len(code_files) > 0:
                files_created = True
                break
        time.sleep(2)
    
    assert files_created, \
        f"Workflow did not create any files in {max_wait} seconds at {workspace_path}"


def test_backend_default_when_not_specified(client):
    """
    Verify backward compatibility - jobs without backend param should work.
    """
    response = client.post(
        '/api/jobs',
        json={'vision': 'test without backend param'},
        content_type='application/json'
    )
    
    assert response.status_code == 201
    job_id = json.loads(response.data)['job_id']
    
    # Should default to OPL and start normally
    time.sleep(5)
    
    response = client.get(f'/api/jobs/{job_id}')
    job = json.loads(response.data)
    
    assert job.get('started_at') is not None, \
        "Job without backend param did not start - backward compatibility broken"


def test_multiple_jobs_can_run_concurrently(client):
    """
    Verify that multiple jobs can be created and started simultaneously.
    """
    job_ids = []
    
    # Create 3 jobs quickly
    for i in range(3):
        response = client.post(
            '/api/jobs',
            json={'vision': f'test job {i}', 'backend': 'opl-ai-team'},
            content_type='application/json'
        )
        assert response.status_code == 201
        job_ids.append(json.loads(response.data)['job_id'])
    
    # Wait for all to start
    time.sleep(10)
    
    # Check all jobs started
    started_count = 0
    for job_id in job_ids:
        response = client.get(f'/api/jobs/{job_id}')
        job = json.loads(response.data)
        if job.get('started_at'):
            started_count += 1
    
    assert started_count >= 2, \
        f"Only {started_count}/3 jobs started - concurrent execution may be broken"


def test_invalid_backend_returns_error(client):
    """
    Verify proper error handling for invalid backend selection.
    """
    response = client.post(
        '/api/jobs',
        json={'vision': 'test', 'backend': 'nonexistent-backend'},
        content_type='application/json'
    )
    
    assert response.status_code == 400
    error = json.loads(response.data)
    assert 'error' in error
    assert 'backend' in error['error'].lower()


@pytest.fixture
def client():
    """Create Flask test client"""
    import sys
    from pathlib import Path
    
    # Add paths
    root = Path(__file__).parent.parent.parent
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / 'agent'))
    sys.path.insert(0, str(root / 'agent' / 'src'))
    
    from crew_studio.llamaindex_web_app import app
    app.config['TESTING'] = True
    
    with app.test_client() as client:
        yield client
