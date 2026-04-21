---
name: python-best-practices
description: Python coding conventions and best practices for the crew
tags:
  - python
  - style
  - testing
---

# Python Best Practices

## Code Style
- Follow PEP 8 and PEP 257 (docstrings).
- Use type hints for all public function signatures.
- Prefer `pathlib.Path` over `os.path`.

## Error Handling
- Catch specific exceptions, never bare `except:`.
- Use `logging` instead of `print()` for diagnostics.
- Fail fast with clear error messages.

## Testing
- Write tests before implementation (TDD).
- Use `pytest` with fixtures and parametrize for coverage.
- Aim for >80% branch coverage on business logic.

## Dependencies
- Pin versions in `pyproject.toml` or `requirements.txt`.
- Prefer the Python standard library when the task is simple enough.
