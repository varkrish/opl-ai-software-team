#!/usr/bin/env python3
"""
End-to-end agent chain test (in-container).

Runs the planning + dev pipeline in production order:
  1. Product Owner  → requirements.md + user_stories.md + features/*.feature
  2. Designer       → design_spec.md
  3. Tech Architect → tech_stack.md + implementation_plan.md
  4. Dev (parallel) → source files from tech_stack tree

Usage:
  podman exec -e PARALLEL_FILE_WORKERS=5 crew-backend-dev \\
    /app/venv/bin/python /app/crew_studio/test_e2e_agents.py
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

from artifact_assertions import (  # noqa: E402
    assert_or_exit,
    validate_design_spec,
    validate_dev_sources,
    validate_po_artifacts,
    validate_tech_stack,
)
from llamaindex_crew.agents.designer_agent import DesignerAgent
from llamaindex_crew.agents.product_owner_agent import ProductOwnerAgent
from llamaindex_crew.agents.tech_architect_agent import TechArchitectAgent
from llamaindex_crew.agents.dev_agent import DevAgent
from llamaindex_crew.config import ConfigLoader
from llamaindex_crew.utils.llm_config import get_supports_react
from llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow

PROJECT_ID = "job-test-e2e-agents-123"
WORKSPACE = Path(f"/app/workspace/{PROJECT_ID}")

VISION = (
    "Develop a CLI tool for data processing. "
    "Read CSV/JSON files, support filter/aggregate/map transforms, "
    "output to stdout or a file. Target users: data analysts."
)


def _header(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def _fail(msg: str) -> None:
    print(f"\n❌ E2E FAILED: {msg}")
    sys.exit(1)


def phase_product_owner() -> str:
    _header("PHASE 1: Product Owner")
    agent = ProductOwnerAgent(workspace_path=WORKSPACE)
    agent.create_user_stories(VISION, context_digest="Greenfield CLI; no legacy code")
    assert_or_exit(validate_po_artifacts(WORKSPACE, VISION), "Product Owner")
    features = list((WORKSPACE / "features").glob("*.feature"))
    print(f"✅ PO artifacts valid — {len(features)} feature file(s)")
    return (WORKSPACE / "user_stories.md").read_text(encoding="utf-8")


def phase_designer(user_stories: str) -> str:
    _header("PHASE 2: Designer")
    agent = DesignerAgent(workspace_path=WORKSPACE)
    agent.create_design_spec(
        user_stories,
        context_digest="Greenfield CLI; no legacy code",
        vision=VISION,
    )
    assert_or_exit(validate_design_spec(WORKSPACE), "Designer")
    design_spec = (WORKSPACE / "design_spec.md").read_text(encoding="utf-8")
    print(f"✅ design_spec.md valid ({len(design_spec)} bytes)")
    return design_spec


def phase_tech_architect(design_spec: str) -> str:
    _header("PHASE 3: Tech Architect")
    agent = TechArchitectAgent(workspace_path=WORKSPACE)
    agent.define_tech_stack(design_spec, VISION, context_digest="Greenfield CLI project")
    assert_or_exit(validate_tech_stack(WORKSPACE), "Tech Architect")
    tech_stack = (WORKSPACE / "tech_stack.md").read_text(encoding="utf-8")
    print(f"✅ tech_stack.md valid ({len(tech_stack)} bytes)")
    return tech_stack


def phase_dev_parallel(tech_stack: str, user_stories: str) -> int:
    _header("PHASE 4: Dev (parallel file generation)")
    workers = int(os.environ.get("PARALLEL_FILE_WORKERS", "5"))
    print(f"supports_react (worker): {get_supports_react('worker')}")
    print(f"PARALLEL_FILE_WORKERS: {workers}")

    try:
        config = ConfigLoader.load()
    except Exception:
        config = None

    workflow = SoftwareDevWorkflow(
        project_id=PROJECT_ID,
        workspace_path=WORKSPACE,
        vision=VISION,
        config=config,
    )
    workflow.tech_stack = tech_stack
    workflow.user_stories = user_stories

    tasks = workflow.task_manager.register_tasks_from_tech_stack(WORKSPACE / "tech_stack.md")
    if not tasks:
        _fail("No file tasks registered from tech_stack.md")

    task_ids = {t.task_id for t in tasks}
    print(f"Registered {len(tasks)} file task(s)")

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

    completed_files: dict = {}
    export_registry: dict = {}
    lock = threading.Lock()

    started = time.monotonic()
    count = workflow._process_file_tasks(
        workflow.dev_agent,
        task_ids,
        "e2e-dev",
        completed_files,
        export_registry,
        lock,
        agent_factory=_make_dev_agent,
    )
    elapsed = time.monotonic() - started

    expected_paths = [
        (t.metadata or {}).get("file_path", "")
        for t in tasks
        if (t.metadata or {}).get("file_path")
    ]
    missing = [p for p in expected_paths if not (WORKSPACE / p).exists()]
    if missing:
        _fail(f"Dev phase missing files: {missing}")

    pending = workflow.task_manager.get_pending_tasks()
    if pending:
        _fail(f"{len(pending)} task(s) still pending after dev phase")

    assert_or_exit(validate_dev_sources(WORKSPACE, expected_paths, min_bytes=30), "Dev")

    print(f"✅ {count}/{len(tasks)} tasks in {elapsed:.1f}s")
    return count


def main() -> None:
    _header("E2E AGENT CHAIN TEST")
    print(f"Workspace: {WORKSPACE}")
    print(f"Vision: {VISION[:80]}...")

    if WORKSPACE.exists():
        shutil.rmtree(WORKSPACE)
    WORKSPACE.mkdir(parents=True, exist_ok=True)

    total_start = time.monotonic()

    user_stories = phase_product_owner()
    design_spec = phase_designer(user_stories)
    tech_stack = phase_tech_architect(design_spec)
    task_count = phase_dev_parallel(tech_stack, user_stories)

    _header("E2E SUMMARY")
    source_files = [
        p for p in WORKSPACE.rglob("*")
        if p.is_file()
        and not p.name.startswith(("tasks_", "state_", "index_"))
        and p.suffix not in (".db",)
    ]
    print(f"Total wall time: {time.monotonic() - total_start:.1f}s")
    print(f"Dev tasks completed: {task_count}")
    print(f"Total workspace files: {len(source_files)}")
    for p in sorted(source_files):
        rel = p.relative_to(WORKSPACE)
        print(f"  {rel} ({p.stat().st_size} B)")

    print("\n✅ E2E PASSED: PO → designer → architect → parallel dev (content validated)")


if __name__ == "__main__":
    main()
