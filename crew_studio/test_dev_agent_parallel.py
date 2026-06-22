"""
Isolated Dev Agent test — parallel file generation via workflow task pool.

Exercises the same path as production:
  TaskManager.register_tasks_from_tech_stack →
  SoftwareDevWorkflow._process_file_tasks(agent_factory=...) →
  _process_file_tasks_parallel (when PARALLEL_FILE_WORKERS > 1)

Usage (inside backend container):
  PARALLEL_FILE_WORKERS=3 podman exec crew-backend-dev \\
    /app/venv/bin/python /app/crew_studio/test_dev_agent_parallel.py
"""
from __future__ import annotations

import os
import shutil
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, "/app/agent/src")
sys.path.insert(0, "/app/crew_studio")

from artifact_assertions import assert_or_exit, validate_dev_sources  # noqa: E402
from llamaindex_crew.agents.dev_agent import DevAgent
from llamaindex_crew.config import ConfigLoader
from llamaindex_crew.orchestrator.task_manager import TaskManager
from llamaindex_crew.utils.llm_config import get_supports_react
from llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow

PROJECT_ID = "job-test-dev-parallel-123"
WORKSPACE = Path(f"/app/workspace/{PROJECT_ID}")

TECH_STACK = """# Technology Stack

**Core Technology**: Python 3.11, Click CLI

**Key Dependencies**:
- click
- pytest

## File Structure
```
dataproc/
├── pyproject.toml
├── src/
│   ├── __init__.py
│   ├── cli.py
│   ├── transforms.py
│   └── test_transforms.py
```
"""

USER_STORIES = """# User Stories

- As a data analyst, I want to filter CSV rows so that I can subset datasets.
- As a user, I want aggregate operations so that I can summarize by column.
- As a developer, I want a CLI entry point so that I can run transforms from the shell.
"""

REQUIREMENTS = """# Requirements

1. CLI reads CSV files from stdin or a path argument.
2. Support filter, aggregate, and map transforms.
3. Output to stdout or an optional output file.
"""


def _setup_workspace() -> None:
    if WORKSPACE.exists():
        shutil.rmtree(WORKSPACE)
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    (WORKSPACE / "tech_stack.md").write_text(TECH_STACK, encoding="utf-8")
    (WORKSPACE / "user_stories.md").write_text(USER_STORIES, encoding="utf-8")
    (WORKSPACE / "requirements.md").write_text(REQUIREMENTS, encoding="utf-8")


def _print_task_summary(task_manager: TaskManager) -> None:
    pending = task_manager.get_pending_tasks()
    print(f"  registered file tasks: {len(pending)}")
    for task in pending:
        fp = (task.metadata or {}).get("file_path", "?")
        print(f"    - {fp} [{task.status}]")


def _print_generated_sources() -> None:
    skip = {"tech_stack.md", "user_stories.md", "requirements.md"}
    skip_prefix = ("tasks_", "state_", "index_")
    for path in sorted(WORKSPACE.rglob("*")):
        if not path.is_file():
            continue
        name = path.name
        if name in skip or name.startswith(skip_prefix):
            continue
        rel = path.relative_to(WORKSPACE)
        print(f"  {rel} ({path.stat().st_size} bytes)")


def main() -> None:
    workers = int(os.environ.get("PARALLEL_FILE_WORKERS", "3"))
    print("Testing Dev Agent — parallel file generation")
    print(f"  workspace: {WORKSPACE}")
    print(f"  PARALLEL_FILE_WORKERS: {workers}")
    print(f"  supports_react (worker model): {get_supports_react('worker')}")

    _setup_workspace()

    try:
        config = ConfigLoader.load()
    except Exception:
        config = None

    vision = "Develop a CLI tool for data processing (filter, aggregate, map on CSV)."
    workflow = SoftwareDevWorkflow(
        project_id=PROJECT_ID,
        workspace_path=WORKSPACE,
        vision=vision,
        config=config,
    )
    workflow.tech_stack = TECH_STACK
    workflow.user_stories = USER_STORIES

    tasks = workflow.task_manager.register_tasks_from_tech_stack(WORKSPACE / "tech_stack.md")
    if not tasks:
        print("FAILED: no file tasks registered from tech_stack.md")
        sys.exit(1)

    task_ids = {t.task_id for t in tasks}
    print("\n=== REGISTERED TASKS ===")
    _print_task_summary(workflow.task_manager)

    backstory = None
    workflow.dev_agent = DevAgent(
        custom_backstory=backstory,
        budget_tracker=workflow.budget_tracker,
        workspace_path=WORKSPACE,
    )

    def _make_dev_agent() -> DevAgent:
        return DevAgent(
            custom_backstory=backstory,
            budget_tracker=workflow.budget_tracker,
            workspace_path=WORKSPACE,
        )

    from llamaindex_crew.tools.file_tools import set_allowed_file_paths

    allowed = workflow.task_manager.get_registered_file_paths()
    set_allowed_file_paths(allowed, workspace=str(WORKSPACE))
    print(f"\n  file_writer allowlist: {len(allowed)} paths")

    completed_files: dict = {}
    export_registry: dict = {}
    lock = threading.Lock()

    print("\n=== RUNNING PARALLEL DEV PHASE ===")
    started = time.monotonic()
    count = workflow._process_file_tasks(
        workflow.dev_agent,
        task_ids,
        "dev-parallel-test",
        completed_files,
        export_registry,
        lock,
        agent_factory=_make_dev_agent,
    )
    elapsed = time.monotonic() - started

    print(f"\n=== PARALLEL RUN COMPLETE ===")
    print(f"  tasks processed: {count}")
    print(f"  elapsed: {elapsed:.1f}s")
    print(f"  completed_files cache: {len(completed_files)}")

    print("\n=== GENERATED FILES ===")
    _print_generated_sources()

    print("\n=== TASK STATUS ===")
    _print_task_summary(workflow.task_manager)

    # Verify every registered file path was created on disk
    expected_paths = [
        (t.metadata or {}).get("file_path", "")
        for t in tasks
        if (t.metadata or {}).get("file_path")
    ]
    missing = [p for p in expected_paths if not (WORKSPACE / p).exists()]
    if missing:
        print("\nTEST FAILED: missing registered files:")
        for p in missing:
            print(f"  - {p}")
        sys.exit(1)

    if count < len(tasks):
        print(f"\nTEST FAILED: only {count}/{len(tasks)} tasks processed")
        sys.exit(1)

    pending = workflow.task_manager.get_pending_tasks()
    if pending:
        print(f"\nTEST FAILED: {len(pending)} task(s) still pending")
        sys.exit(1)

    print("\n=== CONTENT ASSERTIONS ===")
    assert_or_exit(
        validate_dev_sources(WORKSPACE, expected_paths, min_bytes=30),
        "Parallel Dev",
    )

    if workers > 1 and count >= 2:
        print(
            f"\nTEST PASSED: parallel dev generation completed "
            f"({count}/{len(tasks)} tasks, {workers} workers, {elapsed:.1f}s)"
        )
    else:
        print(f"\nTEST PASSED: dev generation completed ({count} tasks, {elapsed:.1f}s)")


if __name__ == "__main__":
    main()
