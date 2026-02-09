# Post-Mortem: Backend Integration Break

## What Went Wrong

**Symptom**: After implementing pluggable agentic backends feature, jobs were created successfully but never started executing. UI showed agents as "idle", Kanban was empty, no progress updates.

**Root Cause**: Duplicate function definition

```python
# Line 51 - EMPTY STUB (accidentally created)
def run_job_async(job_id: str, vision: str, job_config: SecretConfig = None):
    """Run workflow in a separate thread with job-specific workspace"""
    import traceback
    import logging
    # NO CODE HERE!

# Line 95 - REAL IMPLEMENTATION  
def run_job_async(job_id: str, vision: str, job_config: SecretConfig = None):
    """Run workflow in a separate thread with job-specific workspace"""
    # Full implementation with workflow execution
```

Python silently used the first (empty) definition. Thread was created but immediately returned without doing anything.

## Why It Wasn't Caught

### No E2E Tests
- We had API tests (✅ job creation endpoint worked)
- We had no E2E tests (❌ job actually starting and running)
- Gap: Testing the API succeeded but the actual workflow execution was never verified

### False Confidence
- API returned 201 Created
- Job appeared in database with "queued" status
- Everything looked fine from API perspective
- Only manual UI testing revealed the problem

## What We've Added

### 1. Comprehensive E2E Test Suite
**File**: `agent/tests/e2e/test_job_execution.py`

Critical tests that would have caught this:
- `test_job_starts_within_timeout()` - **Would have caught the bug immediately**
- `test_job_progress_updates()` - Verifies workflow -> DB communication
- `test_agents_show_activity()` - Verifies UI will get agent data
- `test_tasks_are_created()` - Verifies Kanban will have data
- `test_workflow_creates_files()` - Ultimate test: did it work?

### 2. Makefile Integration
```bash
make backend-test-e2e-quick  # Run smoke test (~15 seconds)
make backend-test-e2e        # Run full E2E suite
make backend-test-all        # Run API + E2E tests
```

### 3. Testing Strategy Document
**File**: `E2E_TESTING_PLAN.md`

Complete plan for:
- Backend E2E tests
- Frontend Cypress tests
- CI/CD integration
- Test infrastructure

## Lessons Learned

### 1. TDD Would Have Prevented This
Proper flow:
1. Write E2E test first (it fails)
2. Implement feature
3. Run test (it passes)
4. Run ALL tests (nothing breaks)

What actually happened:
1. Implement feature
2. Run existing tests (API tests pass ✅)
3. Assume it works
4. Break during manual testing ❌

### 2. API Tests != E2E Tests
- API tests verify HTTP interface
- E2E tests verify actual behavior
- **Both are needed**

### 3. Critical Path Must Be Tested
The most important flow (job creation -> execution -> completion) had zero automated coverage. This is the ONE thing that absolutely must work.

## Going Forward

### Before Any PR is Merged
1. ✅ API tests pass
2. ✅ E2E tests pass (especially `test_job_starts_within_timeout`)
3. ✅ Manual smoke test in UI

### For Future Features
1. Write E2E test FIRST
2. Watch it fail
3. Implement feature
4. Watch it pass
5. Check no regressions

### CI Integration (TODO)
```yaml
# .github/workflows/test.yml
- name: Run E2E Tests
  run: make backend-test-e2e
  
- name: Fail if jobs don't start
  run: make backend-test-e2e-quick
```

## Current Status

### Fixed ✅
- Removed duplicate `run_job_async` function
- Backend registry working
- GET /api/backends endpoint functional
- Frontend dropdown implemented

### Still Broken ❌
- Jobs still not starting (need to debug further with E2E test)
- UI not showing updates

### Next Steps
1. Run `make backend-test-e2e-quick` to reproduce issue in test
2. Fix until test passes
3. Verify in UI
4. Add to CI

## The Test That Would Have Saved Us

```python
def test_job_starts_within_timeout(client):
    """Job must start within 10 seconds"""
    response = client.post('/api/jobs', json={'vision': 'test'})
    job_id = response.json['job_id']
    
    time.sleep(10)
    job = client.get(f'/api/jobs/{job_id}').json
    
    assert job['started_at'] is not None, "Job did not start!"
    # ^ This would have FAILED and pointed directly to the problem
```

**One simple test. Would have caught the bug in 10 seconds.**

---

*"The best time to write E2E tests was before implementing the feature. The second best time is now."*
