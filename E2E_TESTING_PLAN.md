# E2E Testing Plan for Pluggable Backend Feature

## Problem
The pluggable backend implementation broke core job execution. Jobs are created but don't start running. This should have been caught by automated tests before manual verification.

## Test Coverage Needed

### 1. Core Job Execution (Critical Path)
**File**: `agent/tests/e2e/test_job_execution.py`

```python
def test_job_lifecycle_opl_backend():
    """Test complete job lifecycle with OPL backend"""
    # 1. Create job via API
    # 2. Wait for job to start (status changes to 'running')
    # 3. Verify workflow creates files
    # 4. Verify job completes or progresses
    # Expected: Job should start within 5 seconds
    
def test_job_starts_immediately():
    """Regression test for thread startup bug"""
    # Create job and assert started_at is set within 3 seconds
    
def test_multiple_concurrent_jobs():
    """Test that multiple jobs can run simultaneously"""
    # Create 3 jobs and verify all start
```

### 2. Backend Selection
**File**: `agent/tests/e2e/test_backend_selection.py`

```python
def test_create_job_with_opl_backend():
    """Test job creation with explicit OPL backend"""
    
def test_create_job_with_aider_backend_unavailable():
    """Test error when Aider not installed"""
    
def test_backend_list_endpoint():
    """Test GET /api/backends returns correct backends"""
    
def test_default_backend_when_not_specified():
    """Test that jobs default to OPL when no backend specified"""
```

### 3. UI Integration Tests (Cypress)
**File**: `studio-ui/cypress/e2e/job_creation.cy.ts`

```typescript
describe('Job Creation with Backend Selection', () => {
  it('shows backend selector dropdown', () => {
    // Visit landing page
    // Assert dropdown exists with OPL AI Team option
  })
  
  it('creates and starts job successfully', () => {
    // Select backend
    // Enter vision
    // Submit
    // Wait for build progress to show
    // Assert status changes to "running"
    // Assert agents show activity
  })
  
  it('shows real-time progress updates', () => {
    // Create job
    // Wait for progress updates
    // Assert Kanban board updates
    // Assert agent status changes
  })
})
```

### 4. Integration Tests
**File**: `agent/tests/integration/test_workflow_integration.py`

```python
def test_run_job_async_creates_workflow():
    """Test that run_job_async properly initializes workflow"""
    
def test_workflow_reports_progress():
    """Test that workflow updates job database"""
    
def test_run_job_with_backend_delegates_correctly():
    """Test backend routing logic"""
```

## Implementation Priority

### Phase 1: Critical Path Tests (Do Now)
1. ✅ Basic API test for job creation
2. ❌ **E2E test for job startup** (would have caught current bug)
3. ❌ **E2E test for progress updates** (would have caught UI disconnect)
4. ❌ Integration test for `run_job_async`

### Phase 2: Backend Feature Tests
5. Backend selection API tests
6. UI tests for dropdown
7. Error handling tests

### Phase 3: Performance & Edge Cases  
8. Concurrent job tests
9. Long-running job tests
10. Error recovery tests

## Test Infrastructure Needed

### Backend Testing
```python
# tests/e2e/conftest.py
@pytest.fixture
def flask_app():
    """Start Flask app in test mode"""
    app.config['TESTING'] = True
    return app.test_client()

@pytest.fixture
def wait_for_job_start():
    """Helper to poll until job starts"""
    def _wait(job_id, timeout=10):
        start = time.time()
        while time.time() - start < timeout:
            job = get_job(job_id)
            if job['started_at']:
                return job
            time.sleep(0.5)
        raise TimeoutError(f"Job {job_id} did not start")
    return _wait
```

### Frontend Testing
```typescript
// cypress/support/commands.ts
Cypress.Commands.add('createJob', (vision: string, backend?: string) => {
  cy.visit('/')
  if (backend) {
    cy.get('[data-testid="backend-selector"]').select(backend)
  }
  cy.get('[data-testid="vision-input"]').type(vision)
  cy.get('[data-testid="create-button"]').click()
})

Cypress.Commands.add('waitForJobToStart', (timeout = 10000) => {
  cy.get('[data-testid="job-status"]', { timeout })
    .should('contain', 'running')
})
```

## Makefile Targets

```makefile
test-e2e:
\t@echo "Running E2E tests..."
\tcd agent && PYTHONPATH=$(PWD):$(PWD)/agent:$(PWD)/agent/src pytest tests/e2e/ -v --tb=short

test-e2e-watch:
\t@echo "Running E2E tests in watch mode..."
\tcd agent && PYTHONPATH=$(PWD):$(PWD)/agent:$(PWD)/agent/src pytest-watch tests/e2e/

test-ui-e2e:
\t@echo "Running UI E2E tests..."
\tcd studio-ui && npm run cypress:run

test-all:
\tmake test-unit && make test-api && make test-e2e && make test-ui-e2e
```

## CI Integration

### GitHub Actions Workflow
```yaml
name: E2E Tests

on: [push, pull_request]

jobs:
  e2e-backend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Set up Python
        uses: actions/setup-python@v4
      - name: Install dependencies
        run: pip install -r agent/requirements.txt
      - name: Run E2E tests
        run: make test-e2e
        
  e2e-frontend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Start backend
        run: make backend &
      - name: Build frontend
        run: cd studio-ui && npm ci && npm run build
      - name: Run Cypress
        run: make test-ui-e2e
```

## Current Bug - Root Cause Analysis

### What Happened
1. Added `run_job_with_backend()` function
2. Accidentally created duplicate `run_job_async()` stub
3. Python used the empty stub instead of real implementation
4. Jobs were created but threads never executed
5. No tests caught this - required manual debugging

### What E2E Test Would Have Caught
```python
def test_job_starts_within_timeout():
    """REGRESSION TEST: Job must start within 5 seconds"""
    # Create job
    response = client.post('/api/jobs', json={'vision': 'test'})
    job_id = response.json['job_id']
    
    # Wait for job to start
    time.sleep(5)
    job = client.get(f'/api/jobs/{job_id}').json
    
    # ASSERT: Job should have started
    assert job['started_at'] is not None, "Job did not start!"
    assert job['status'] == 'running', f"Job status is {job['status']}, expected 'running'"
```

This simple test would have failed immediately and pointed to the exact problem.

## Recommendation

Before proceeding with any new features:
1. **Write the E2E test first** (TDD approach)
2. **Run test** - it should fail
3. **Implement feature**
4. **Run test** - it should pass
5. **Run full test suite** - nothing should break

This would have prevented the current situation where we have a feature "implemented" but core functionality is broken.
