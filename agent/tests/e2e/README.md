# E2E Tests

End-to-end tests for the AI Software Development Crew system.

## Test Structure

```
tests/e2e/
├── conftest.py                    # E2E fixtures and helpers
├── test_workflow_e2e.py          # Core workflow E2E tests
├── test_web_api_e2e.py           # Web API E2E tests
├── test_web_ui_playwright.py     # UI E2E tests (Playwright)
└── test_calculator_complete.py   # Complete calculator example
```

## Running E2E Tests

### Prerequisites

1. **API Keys**: E2E tests require either `OPENROUTER_API_KEY` or `OPENAI_API_KEY` in your `.env` file
2. **Dependencies**: Install test dependencies:
   ```bash
   pip install -e ".[test]"
   ```

3. **For UI tests**: Install Playwright browsers:
   ```bash
   playwright install chromium
   ```

### Run All E2E Tests

```bash
# All E2E tests (slow, requires API keys)
pytest tests/e2e/ -m e2e

# Workflow tests only
pytest tests/e2e/test_workflow_e2e.py

# API tests only
pytest tests/e2e/test_web_api_e2e.py -m api

# UI tests only (requires Playwright)
pytest tests/e2e/test_web_ui_playwright.py -m ui
```

### Run Specific Tests

```bash
# Calculator workflow E2E
pytest tests/e2e/test_workflow_e2e.py::test_calculator_workflow_e2e

# TODO API workflow E2E
pytest tests/e2e/test_workflow_e2e.py::test_todo_api_workflow_e2e

# Complete calculator example
pytest tests/e2e/test_calculator_complete.py
```

### Skip Slow Tests

```bash
# Skip slow tests (> 5 minutes)
pytest tests/e2e/ -m "e2e and not slow"
```

## Test Categories

### Workflow E2E Tests (`test_workflow_e2e.py`)

Tests the complete software development workflow:
- ✅ Calculator generation (simple Python project)
- ✅ TODO API generation (FastAPI project)
- ✅ State machine transitions
- ✅ Minimal vision handling
- ✅ Technology detection from vision

**Cost**: $0.50 - $2.00 per test (uses OpenRouter/OpenAI)

### Web API E2E Tests (`test_web_api_e2e.py`)

Tests the Flask REST API:
- ✅ Create build jobs
- ✅ Get job status
- ✅ List jobs
- ✅ Job execution flow
- ✅ Concurrent jobs
- ✅ Error handling

**Cost**: Free (no LLM calls, only API testing)

### Web UI E2E Tests (`test_web_ui_playwright.py`)

Tests the web interface:
- ✅ Homepage loads
- ✅ Create job from UI
- ✅ View job status
- ✅ Form validation
- ✅ Progress updates
- ✅ Responsive design

**Cost**: Free (no LLM calls, only UI testing)

## Test Fixtures

### `e2e_workspace`
Provides isolated workspace directory for each test.

### `calculator_vision`
Standard calculator vision for consistent testing.

### `todo_api_vision`
TODO API vision for more complex testing.

### `verify_workflow_outputs()`
Helper function to verify generated files and artifacts.

## Configuration

Tests use the following environment variables:
- `OPENROUTER_API_KEY` or `OPENAI_API_KEY`: Required for workflow tests
- `BUDGET_MAX_COST_PER_PROJECT`: Set to $10 for E2E tests
- `BUDGET_MAX_COST_PER_HOUR`: Set to $5 for E2E tests

## Timeout Settings

- Default: 5 minutes (300 seconds)
- Slow tests: 15 minutes (900 seconds)
- Can be overridden: `pytest --timeout=600`

## CI/CD Integration

### GitHub Actions Example

```yaml
name: E2E Tests

on: [push, pull_request]

jobs:
  e2e:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      
      - name: Install dependencies
        run: |
          pip install -e ".[test]"
          playwright install chromium
      
      - name: Run E2E tests
        env:
          OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
        run: |
          pytest tests/e2e/ -m "e2e and not slow" --timeout=600
```

## Troubleshooting

### Test Failures

1. **API Key Issues**:
   ```bash
   # Check your .env file
   cat .env | grep API_KEY
   ```

2. **Budget Exceeded**:
   - E2E tests have a $10 budget limit
   - Check `BUDGET_MAX_COST_PER_PROJECT` in test environment

3. **Timeout Issues**:
   ```bash
   # Increase timeout for slow tests
   pytest tests/e2e/ --timeout=900
   ```

4. **Playwright Issues**:
   ```bash
   # Reinstall browsers
   playwright install --force chromium
   ```

### Cost Control

E2E tests can be expensive. To minimize costs:

1. **Use OpenRouter Free Models**:
   ```bash
   LLM_MODEL_MANAGER=x-ai/grok-4.1-fast:free
   ```

2. **Run Specific Tests**:
   ```bash
   # Only run fast, cheap tests
   pytest tests/e2e/test_web_api_e2e.py
   ```

3. **Skip Workflow Tests Locally**:
   ```bash
   # Skip tests that require LLM calls
   pytest tests/e2e/ -m "e2e and not requires_api_key"
   ```

## Development

### Adding New E2E Tests

1. Create test function in appropriate file
2. Use `@pytest.mark.e2e` decorator
3. Use `@pytest.mark.timeout(seconds)` if test is slow
4. Use fixtures from `conftest.py`
5. Verify outputs with `verify_workflow_outputs()`

Example:
```python
@pytest.mark.e2e
@pytest.mark.timeout(600)
def test_my_workflow(e2e_workspace):
    vision = "My test vision"
    workflow = SoftwareDevWorkflow(
        project_id="my_test",
        workspace_path=e2e_workspace,
        vision=vision
    )
    results = workflow.run()
    assert results["status"] == "completed"
```

### Debugging E2E Tests

```bash
# Run with verbose output and no capture
pytest tests/e2e/test_workflow_e2e.py::test_calculator_workflow_e2e -vv -s

# Run with pdb on failure
pytest tests/e2e/ --pdb

# Show print statements
pytest tests/e2e/ -s
```

## Performance

| Test Category | Avg Time | Cost | Parallelizable |
|---------------|----------|------|----------------|
| Workflow E2E  | 5-10 min | $1-2 | No (API rate limits) |
| API E2E       | < 30 sec | Free | Yes |
| UI E2E        | < 2 min  | Free | Yes |

## Future Enhancements

- [ ] Add snapshot testing for generated code
- [ ] Add performance benchmarks
- [ ] Add E2E tests for error recovery
- [ ] Add E2E tests for budget exceeded scenarios
- [ ] Add visual regression testing for UI
- [ ] Add load testing for API
- [ ] Add E2E tests for different tech stacks (React, Vue, etc.)
