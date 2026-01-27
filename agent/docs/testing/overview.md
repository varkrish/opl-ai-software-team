# Testing Overview

The AI Software Development Crew has a comprehensive test suite organized by test type and purpose.

## Test Structure

```
tests/
├── unit/                  # Fast, isolated unit tests
│   ├── test_state_machine.py
│   └── test_task_manager.py
├── integration/           # Tests with dependencies
│   └── test_workflow.py
├── e2e/                   # ⭐ End-to-end tests
│   ├── test_workflow_e2e.py
│   ├── test_web_api_e2e.py
│   ├── test_web_ui_playwright.py
│   └── test_calculator_complete.py
├── api/                   # API endpoint tests
│   └── test_web_app.py
└── frontend/              # Frontend component tests
    └── test_web_ui_basic.py
```

## Test Categories

| Category | Speed | Cost | Description |
|----------|-------|------|-------------|
| **Unit** | < 1s | FREE | Test individual functions/classes |
| **Integration** | < 30s | FREE | Test component interactions |
| **E2E** | 5-10min | $1-2 | Test complete workflow |
| **API** | < 5s | FREE | Test REST endpoints |
| **UI** | < 2min | FREE | Test web interface |

## Quick Start

### Using Make

```bash
# Run fast tests
make test-quick

# Run with coverage
make test-coverage

# Run E2E tests (requires API key)
make test-e2e

# Run specific category
make test-unit
make test-api
make test-ui
```

### Using Pytest Directly

```bash
# Set PYTHONPATH
export PYTHONPATH=$(pwd)/src

# Run all tests
pytest

# Run by marker
pytest -m unit
pytest -m integration
pytest -m e2e
pytest -m api
pytest -m ui

# Run specific test
pytest tests/e2e/test_workflow_e2e.py::test_calculator_workflow_e2e
```

## Test Markers

Tests are organized using pytest markers:

```python
@pytest.mark.unit          # Unit test
@pytest.mark.integration   # Integration test
@pytest.mark.e2e          # E2E test
@pytest.mark.api          # API test
@pytest.mark.ui           # UI test
@pytest.mark.slow         # Slow test (> 60s)
```

## Running Specific Test Suites

### Unit Tests (Fast, FREE)

```bash
make test-unit
# or
pytest tests/unit/ -m unit -v
```

**What they test:**
- State machine transitions
- Task manager operations
- Budget calculations
- Utility functions

### Integration Tests (Medium, FREE)

```bash
make test-integration
# or
pytest tests/integration/ -m integration -v
```

**What they test:**
- Workflow initialization
- Agent interactions
- Tool integrations
- Database operations

### E2E Tests (Slow, $1-2)

```bash
# Set API key first
export OPENROUTER_API_KEY=your_key

make test-e2e
# or
pytest tests/e2e/ -m e2e -v
```

**What they test:**
- Complete workflow from vision to code
- Calculator generation
- TODO API generation
- State transitions
- Budget tracking

### API Tests (Fast, FREE)

```bash
make test-api
# or
pytest tests/e2e/test_web_api_e2e.py -m api -v
```

**What they test:**
- Job creation
- Status retrieval
- Job listing
- Concurrent jobs
- Error handling

### UI Tests (Medium, FREE)

```bash
# Install Playwright first
make install-playwright

make test-ui
# or
pytest tests/e2e/test_web_ui_playwright.py -m ui -v
```

**What they test:**
- Homepage loading
- Form submission
- Job status display
- Progress updates
- Responsive design

## Coverage

```bash
# Generate coverage report
make test-coverage

# View HTML report
open htmlcov/index.html

# View terminal report
pytest --cov=src --cov-report=term-missing
```

**Coverage Goals:**
- Overall: 80%+
- Core modules: 90%+
- New features: 100%

## CI/CD Integration

### GitHub Actions Example

```yaml
name: Tests

on: [push, pull_request]

jobs:
  quick-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - run: make ci-install
      - run: make ci-test
  
  e2e-tests:
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main'
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - run: make ci-install
      - run: make ci-test-e2e
        env:
          OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
```

## Best Practices

1. **Write unit tests first** - Fast feedback loop
2. **Use fixtures** - DRY principle, consistent test data
3. **Mock external dependencies** - Keep tests fast and isolated
4. **Test edge cases** - Error handling, empty inputs, boundary conditions
5. **Keep E2E tests minimal** - They're expensive in time and cost
6. **Use descriptive names** - Test names should describe what they test
7. **Run tests before committing** - `make test-quick`
8. **Check coverage** - Aim for 80%+
9. **Document complex tests** - Explain the "why"
10. **Clean up resources** - Use fixtures with teardown

## Debugging Tests

```bash
# Show print statements
pytest -s

# Drop into debugger on failure
pytest --pdb

# Run last failed tests
pytest --lf

# Very verbose output
pytest -vv

# Show local variables on failure
pytest -l
```

## Performance

| Test Type | Count | Avg Time | Total Time | Parallel |
|-----------|-------|----------|------------|----------|
| Unit | 10-20 | < 1s | < 20s | ✅ Yes |
| Integration | 5-10 | < 30s | < 5min | ✅ Yes |
| API | 10-15 | < 5s | < 1min | ✅ Yes |
| UI | 5-10 | < 2min | < 20min | ✅ Yes |
| E2E | 5-10 | 5-10min | 30-60min | ❌ No (API limits) |

## Next Steps

- [Unit Tests](unit-tests.md) - Learn about unit testing
- [Integration Tests](integration-tests.md) - Component integration testing
- [E2E Tests](e2e-tests.md) - End-to-end workflow testing
- [Running Tests](running-tests.md) - Detailed test running guide
