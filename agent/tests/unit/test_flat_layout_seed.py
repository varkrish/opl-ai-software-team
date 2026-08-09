"""
A flat project layout must not lose its application source.

``_build_path_only_contract_from_specs`` keys packages on each path's parent
directory and skips anything whose parent is ``"."``::

    for p in sorted(paths):
        parent_dir = str(Path(p).parent)
        if parent_dir and parent_dir != ".":
            packages.setdefault(parent_dir, ...)

    # Safety net: root-level files alone must still produce a non-empty map
    if not packages and paths:
        packages["."] = {...}

The safety net only fires when the map is *entirely* empty. One subdirectory is
enough to defeat it, and ``tests/`` is present in essentially every generated
project — so for a flat layout the app source is dropped and only the tests
survive.

Live, job 1cec01ad. tech_stack.md declared::

    ├── main.py
    ├── models.py
    ├── schemas.py
    ├── service.py
    └── tests/
        ├── __init__.py
        └── test_service.py

and the locked contract contained exactly one package::

    tests -> tests/__init__.py, tests/conftest.py, tests/test_api.py, ...

``files_from_contract`` feeds ``build_creation_manifest``, so the whole
application was missing from the creation manifest while the test files were
registered — the contract described a project consisting only of tests for code
nothing had been asked to write.

Flat layouts are the norm for small Python and Go projects, which is exactly the
size of project a 14b is asked to produce.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from llamaindex_crew.utils.wiring_contract import (  # noqa: E402
    _build_path_only_contract_from_specs,
    files_from_contract,
)

FLAT_TECH_STACK = """
# Expense Tracker

## File Structure

```
expense-tracker/
├── main.py
├── models.py
├── schemas.py
├── service.py
└── tests/
    ├── __init__.py
    └── test_service.py
```
"""


def _all_files(contract):
    return {
        f
        for pkg in (contract.get("packages") or {}).values()
        for f in (pkg.get("files") or [])
    }


def test_root_level_sources_survive_alongside_a_subdirectory():
    """The live regression: tests/ existed, so the app source was dropped."""
    contract = _build_path_only_contract_from_specs("", "", tech_stack=FLAT_TECH_STACK)

    files = _all_files(contract)
    for expected in ("main.py", "models.py", "schemas.py", "service.py"):
        assert expected in files, (
            f"{expected} was declared in the file tree but is missing from the "
            f"contract; got {sorted(files)}"
        )


def test_the_subdirectory_package_is_still_declared():
    contract = _build_path_only_contract_from_specs("", "", tech_stack=FLAT_TECH_STACK)

    assert "tests" in (contract.get("packages") or {})
    assert "tests/test_service.py" in _all_files(contract)


def test_root_sources_reach_the_creation_manifest():
    """files_from_contract is what actually registers file_creation tasks."""
    contract = _build_path_only_contract_from_specs("", "", tech_stack=FLAT_TECH_STACK)

    paths = {e["path"] for e in files_from_contract(contract)}

    assert {"main.py", "models.py", "schemas.py", "service.py"} <= paths


def test_root_only_project_still_works():
    """The original safety net's case must keep working."""
    spec = """
```
├── main.py
├── util.py
```
"""
    contract = _build_path_only_contract_from_specs("", "", tech_stack=spec)

    assert _all_files(contract) == {"main.py", "util.py"}


def test_nested_only_layout_is_unchanged():
    spec = """
```
sandbox-api/
├── cmd/
│   └── server/
│       └── main.go
└── internal/
    └── api/
        └── handler.go
```
"""
    contract = _build_path_only_contract_from_specs("", "", tech_stack=spec)
    packages = contract.get("packages") or {}

    assert "." not in packages, "no root-level sources here, so no root package"
    assert "cmd/server" in packages and "internal/api" in packages


def test_no_paths_at_all_yields_no_packages():
    contract = _build_path_only_contract_from_specs("", "", tech_stack="# Just prose\n")
    assert (contract.get("packages") or {}) == {}


def test_root_package_does_not_swallow_subdirectory_files():
    contract = _build_path_only_contract_from_specs("", "", tech_stack=FLAT_TECH_STACK)
    root_files = ((contract.get("packages") or {}).get(".") or {}).get("files") or []

    assert all("/" not in f for f in root_files), (
        f"the root package must hold only root-level files, got {root_files}"
    )
