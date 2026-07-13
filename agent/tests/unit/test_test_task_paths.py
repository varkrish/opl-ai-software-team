"""Unit tests for TDD test path derivation and registration."""
import shutil
import tempfile
from pathlib import Path

import pytest

from llamaindex_crew.orchestrator.task_manager import TaskManager
from llamaindex_crew.utils.test_task_paths import (
    derive_mirror_test_path,
    derive_tdd_test_paths,
    extract_test_paths_from_plan,
)


TRAVEL_TECH_STACK = """
```text
travel_planner/
├── api/main.py
├── agents/place_search/agent.py
├── db/models.py
├── tools/google_places.py
```
"""

TRAVEL_TEST_PLAN = """
* **File layout** – tests under `tests/` (e.g., `tests/agents/test_place_search.py`).
Fixtures in `tests/fixtures/trip_request.json`.
Helper in `tests/conftest.py`. Factories live in `tests/factories.py`.
backend_test_dir: tests
"""


def test_derive_mirror_agent_module():
    assert derive_mirror_test_path("agents/place_search/agent.py") == (
        "tests/agents/test_place_search.py"
    )


def test_derive_mirror_api_main():
    assert derive_mirror_test_path("api/main.py") == "tests/api/test_main.py"


def test_derive_mirror_skips_init():
    assert derive_mirror_test_path("db/__init__.py") is None


def test_extract_test_paths_from_plan():
    paths = extract_test_paths_from_plan(TRAVEL_TEST_PLAN)
    assert "tests/agents/test_place_search.py" in paths
    assert "tests/conftest.py" in paths
    assert "tests/factories.py" in paths
    assert "tests/fixtures/trip_request.json" in paths


def test_derive_tdd_test_paths_combines_mirror_and_plan():
    source = ["agents/place_search/agent.py", "api/main.py", "db/models.py"]
    paths = derive_tdd_test_paths(source, TRAVEL_TEST_PLAN)
    assert "tests/agents/test_place_search.py" in paths
    assert "tests/api/test_main.py" in paths
    assert "tests/db/test_models.py" in paths
    assert "tests/conftest.py" in paths
    assert "tests/factories.py" in paths


@pytest.fixture
def workspace():
    tmp = tempfile.mkdtemp()
    yield Path(tmp)
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def task_mgr(workspace):
    db_path = workspace / "tasks_test.db"
    return TaskManager(db_path, "test-project")


def test_register_tdd_test_tasks_idempotent(task_mgr, workspace):
    tech_stack_path = workspace / "tech_stack.md"
    tech_stack_path.write_text(TRAVEL_TECH_STACK, encoding="utf-8")
    plan_path = workspace / "test_plan.md"
    plan_path.write_text(TRAVEL_TEST_PLAN, encoding="utf-8")

    task_mgr.register_granular_tasks("", TRAVEL_TECH_STACK, tdd=True)
    before = [
        t for t in task_mgr.get_all_tasks()
        if (t.metadata or {}).get("file_path", "").startswith("tests/")
    ]
    assert before, "TDD registration should add tests/ file_creation tasks"

    again = task_mgr.register_tdd_test_tasks(TRAVEL_TECH_STACK, TRAVEL_TEST_PLAN)
    assert again == []

    test_paths = {
        (t.metadata or {}).get("file_path")
        for t in task_mgr.get_all_tasks()
        if (t.metadata or {}).get("file_path", "").startswith("tests/")
    }
    assert "tests/agents/test_place_search.py" in test_paths
    assert "tests/conftest.py" in test_paths

    tiers = [
        task_mgr._classify_file_tier(
            (t.metadata or {}).get("file_path", ""), tdd=True,
        )
        for t in task_mgr.get_all_tasks()
        if (t.metadata or {}).get("file_path", "").startswith("tests/")
    ]
    assert all(t == TaskManager._TEST_FILE_TIER_TDD for t in tiers)
