# Testing Guide

## Overview

This project has a comprehensive test suite organized by test type and purpose.

## Quick Start

```bash
# Run fast tests
./run_tests.sh quick

# Run with coverage
./run_tests.sh coverage

# Run E2E tests
./run_tests.sh e2e
```

## Test Structure

```
tests/
├── unit/                  # Fast, isolated unit tests
│   ├── test_state_machine.py
│   └── test_task_manager.py
├── integration/           # Tests with dependencies (DB, filesystem)
│   └── test_workflow.py
├── e2e/                   # End-to-end tests (full workflow)
│   ├── conftest.py                  # E2E fixtures
│   ├── test_workflow_e2e.py         # Workflow E2E tests
│   ├── test_web_api_e2e.py          # API E2E tests  
│   ├── test_web_ui_playwright.py    # UI E2E tests
│   ├── test_calculator_complete.py  # Complete example
│   └── README.md                    # Detailed E2E docs
├── api/                   # API endpoint tests
│   └── test_web_app.py
├── frontend/              # Frontend component tests
│   └── test_web_ui_basic.py
└── conftest.py            # Global pytest fixtures
```

## Running Tests

### Using Test Runner Script (Recommended)

```bash
# Unit tests (fast, < 1 second)
./run_tests.sh unit

# Integration tests (< 30 seconds)
./run_tests.sh integration

# E2E tests (slow, 5-10 minutes, requires API keys)
./run_tests.sh e2e

# Fast E2E only (skip slow tests)
./run_tests.sh e2e-fast

# API tests
./run_tests.sh api

# UI tests (requires Playwright)
./run_tests.sh ui

# All tests except E2E
./run_tests.sh all

# All tests including E2E
./run_tests.sh all-with-e2e

# Coverage report
./run_tests.sh coverage

# Quick smoke test
./run_tests.sh quick

# Specific E2E tests
./run_tests.sh calculator
./run_tests.sh workflow
```

### Using Pytest Directly

```bash
# Install test dependencies
pip install -e ".[test]"

# Run all tests
pytest

# Run by marker
pytest -m unit
pytest -m integration
pytest -m e2e
pytest -m api
pytest -m ui
pytest -m slow

# Run by directory
pytest tests/unit/
pytest tests/integration/
pytest tests/e2e/

# Run specific test
pytest tests/e2e/test_workflow_e2e.py::test_calculator_workflow_e2e

# Run with options
pytest -v              # Verbose
pytest -s              # Show print statements
pytest -x              # Stop on first failure
pytest --pdb           # Drop into debugger on failure
pytest --lf            # Run last failed tests
pytest --tb=short      # Short traceback format

# Run with coverage
pytest --cov=src --cov-report=html
pytest --cov=src --cov-report=term-missing

# Run with timeout
pytest --timeout=300

# Skip slow tests
pytest -m "not slow"

# Run tests in parallel (requires pytest-xdist)
pytest -n auto
```

## Test Categories

### Unit Tests
- **Speed**: < 1 second per test
- **Cost**: Free
- **Purpose**: Test individual functions/classes in isolation
- **Requirements**: None
- **Examples**: State machine, task manager, budget tracker

```bash
pytest tests/unit/ -m unit
```

### Integration Tests
- **Speed**: < 30 seconds per test
- **Cost**: Free
- **Purpose**: Test interactions between components
- **Requirements**: Filesystem, SQLite
- **Examples**: Workflow initialization, task persistence

```bash
pytest tests/integration/ -m integration
```

### E2E Tests
- **Speed**: 5-10 minutes per test
- **Cost**: $1-2 per test (uses LLM APIs)
- **Purpose**: Test complete workflow from vision to code
- **Requirements**: OPENROUTER_API_KEY or OPENAI_API_KEY
- **Examples**: Calculator generation, TODO API generation

```bash
# Set API key first
export OPENROUTER_API_KEY=your_key_here

# Run E2E tests
pytest tests/e2e/ -m e2e
```

### API Tests
- **Speed**: < 30 seconds
- **Cost**: Free
- **Purpose**: Test REST API endpoints
- **Requirements**: None
- **Examples**: Job creation, status retrieval, job listing

```bash
pytest tests/e2e/test_web_api_e2e.py -m api
```

### UI Tests
- **Speed**: < 2 minutes per test
- **Cost**: Free
- **Purpose**: Test web interface interactions
- **Requirements**: Playwright
- **Examples**: Form submission, job status display

```bash
# Install Playwright first
playwright install chromium

# Run UI tests
pytest tests/e2e/test_web_ui_playwright.py -m ui
```

## Test Fixtures

### Global Fixtures (`tests/conftest.py`)
- `test_workspace`: Session-scoped temporary workspace
- `set_test_env`: Auto-used environment setup

### E2E Fixtures (`tests/e2e/conftest.py`)
- `e2e_base_workspace`: Base E2E workspace
- `e2e_workspace`: Test-specific E2E workspace
- `calculator_vision`: Standard calculator vision
- `todo_api_vision`: TODO API vision
- `verify_workflow_outputs()`: Helper to verify generated files

## Configuration

### pytest.ini
```ini
[pytest]
markers =
    unit: Unit tests
    integration: Integration tests
    e2e: End-to-end tests
    api: API tests
    ui: UI tests
    slow: Slow tests (> 60 seconds)
    requires_api_key: Requires API keys

timeout = 300  # 5 minutes default
```

### Environment Variables
```bash
# Required for E2E tests
OPENROUTER_API_KEY=your_key_here
# or
OPENAI_API_KEY=your_key_here

# Optional
BUDGET_MAX_COST_PER_PROJECT=10.0
BUDGET_MAX_COST_PER_HOUR=5.0
WORKSPACE_PATH=./workspace
```

## CI/CD Integration

### GitHub Actions Example

```yaml
name: Tests

on: [push, pull_request]

jobs:
  unit-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - run: pip install -e ".[test]"
      - run: pytest tests/unit/ -m unit
  
  integration-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - run: pip install -e ".[test]"
      - run: pytest tests/integration/ -m integration
  
  e2e-tests:
    runs-on: ubuntu-latest
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - run: pip install -e ".[test]"
      - run: pytest tests/e2e/ -m "e2e and not slow"
        env:
          OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
```

## Coverage

```bash
# Generate HTML coverage report
pytest --cov=src --cov-report=html

# Open in browser
open htmlcov/index.html

# Terminal report with missing lines
pytest --cov=src --cov-report=term-missing

# Target 80%+ coverage for production code
```

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

# Run specific test with all debug info
pytest tests/e2e/test_workflow_e2e.py::test_calculator_workflow_e2e -vv -s --pdb
```

## Performance

| Test Type | Count | Avg Time | Total Time |
|-----------|-------|----------|------------|
| Unit | 10-20 | < 1s | < 20s |
| Integration | 5-10 | < 30s | < 5min |
| E2E | 5-10 | 5-10min | 30-60min |
| API | 10-15 | < 5s | < 1min |
| UI | 5-10 | < 2min | < 20min |

## Best Practices

1. **Write unit tests first** - Fast feedback loop
2. **Use markers** - Organize tests by type
3. **Mock external dependencies** - Keep tests fast
4. **Use fixtures** - DRY principle
5. **Test edge cases** - Error handling, empty inputs
6. **Keep E2E tests minimal** - They're expensive
7. **Run tests before committing** - `./run_tests.sh quick`
8. **Check coverage** - Aim for 80%+
9. **Document complex tests** - Explain the "why"
10. **Clean up resources** - Use fixtures with cleanup

## Troubleshooting

### Tests Failing
```bash
# Check environment
pytest --version
python --version

# Check dependencies
pip list | grep pytest

# Clean and reinstall
pip uninstall -y pytest pytest-cov pytest-asyncio
pip install -e ".[test]"
```

### E2E Tests Timing Out
```bash
# Increase timeout
pytest tests/e2e/ --timeout=900

# Run with more verbose output
pytest tests/e2e/ -vv -s
```

### Playwright Issues
```bash
# Reinstall browsers
playwright install --force chromium

# Run with headed mode for debugging
# Modify test to use headless=False
```

### Coverage Not Working
```bash
# Ensure pytest-cov is installed
pip install pytest-cov

# Check source path
pytest --cov=src --cov-report=term
```

## Adding New Tests

### Unit Test Example
```python
# tests/unit/test_new_feature.py
import pytest

@pytest.mark.unit
def test_new_feature():
    result = my_function()
    assert result == expected
```

### E2E Test Example
```python
# tests/e2e/test_new_workflow.py
import pytest

@pytest.mark.e2e
@pytest.mark.timeout(600)
def test_new_workflow(e2e_workspace):
    workflow = SoftwareDevWorkflow(
        project_id="test",
        workspace_path=e2e_workspace,
        vision="..."
    )
    results = workflow.run()
    assert results["status"] == "completed"
```

## Resources

- [Pytest Documentation](https://docs.pytest.org/)
- [Playwright Documentation](https://playwright.dev/python/)
- [E2E Tests README](tests/e2e/README.md)
- [Coverage.py Documentation](https://coverage.readthedocs.io/)
