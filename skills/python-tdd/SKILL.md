---
name: python-tdd
description: >-
  Test-Driven Development patterns for Python projects. Covers pytest fixtures,
  mocking, and Red-Green-Refactor cycle. Use when writing Python tests.
tags: [python, testing, tdd]
---

# Python TDD Patterns

## Red-Green-Refactor

1. Write a failing test (Red)
2. Write minimal code to make it pass (Green)
3. Refactor while keeping tests green

## Fixtures

```python
@pytest.fixture
def db_session(tmp_path):
    db = Database(tmp_path / "test.db")
    yield db
    db.close()
```

## Mocking

Use `unittest.mock.patch` to isolate units:

```python
with patch("myapp.api.external_call", return_value={"ok": True}):
    result = myapp.api.process()
    assert result.success
```
