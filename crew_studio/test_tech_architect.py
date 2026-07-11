#!/usr/bin/env python3
"""
Run ONLY the Tech Architect agent (3-pass: stack → file tree → impl plan).

Usage (in container):

  # Reuse design_spec + solution_spec from an existing job workspace:
  podman exec crew-backend-prod \\
    env JOB_ID=a83b36dd-e775-4be8-be33-bb9ca9225f51 \\
    /opt/app-root/bin/python3 /app/crew_studio/test_tech_architect.py

  # Fresh mini workspace with env overrides:
  podman exec crew-backend-prod \\
    env VISION="Build a CLI CSV tool" \\
    /opt/app-root/bin/python3 /app/crew_studio/test_tech_architect.py

  # Point at any workspace path:
  podman exec crew-backend-prod \\
    env WORKSPACE=/app/workspace/job-my-test \\
    /opt/app-root/bin/python3 /app/crew_studio/test_tech_architect.py
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, "/app/agent/src")
sys.path.insert(0, "/app/crew_studio")

from artifact_assertions import assert_or_exit, validate_tech_stack  # noqa: E402
from llamaindex_crew.agents.tech_architect_agent import TechArchitectAgent  # noqa: E402
from llamaindex_crew.orchestrator.task_manager import TaskManager  # noqa: E402


DEFAULT_VISION = (
    "Create a Frappe App for Movie Ticket Management. "
    "Include payment using stripe and reuse erpnext modules."
)
DEFAULT_DESIGN_SPEC = (
    "Modules: Ticketing. DocTypes: Screen, Seat, Showtime, Booking, Payment. "
    "Integrate Stripe. Webhooks in hooks.py."
)


def _resolve_workspace() -> Path:
    job_id = os.environ.get("JOB_ID", "").strip()
    if job_id:
        return Path(f"/app/workspace/job-{job_id}")
    ws = os.environ.get("WORKSPACE", "").strip()
    if ws:
        return Path(ws)
    return Path("/app/workspace/job-test-architect-123")


def _load_inputs(workspace: Path) -> tuple[str, str, str, bool, str]:
    """Return vision, design_spec, user_stories, approved_solution, solution_spec."""
    fresh = os.environ.get("FRESH", "").lower() in ("1", "true", "yes")
    if fresh or not (workspace / "design_spec.md").exists():
        workspace.mkdir(parents=True, exist_ok=True)
        return (
            os.environ.get("VISION", DEFAULT_VISION),
            os.environ.get("DESIGN_SPEC", DEFAULT_DESIGN_SPEC),
            os.environ.get("USER_STORIES", ""),
            False,
            "",
        )

    design_spec = (workspace / "design_spec.md").read_text(encoding="utf-8", errors="replace")
    user_stories = ""
    if (workspace / "user_stories.md").exists():
        user_stories = (workspace / "user_stories.md").read_text(encoding="utf-8", errors="replace")
    solution_spec = ""
    if (workspace / "solution_spec.md").exists():
        solution_spec = (workspace / "solution_spec.md").read_text(encoding="utf-8", errors="replace")
    vision = os.environ.get("VISION", "").strip()
    if not vision and (workspace / "crew_errors.log").exists():
        for line in (workspace / "crew_errors.log").read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("Vision:"):
                vision = line.split("Vision:", 1)[1].strip()
                break
    if not vision:
        vision = DEFAULT_VISION
    approved = bool(solution_spec.strip())
    return vision, design_spec, user_stories, approved, solution_spec


def _validate_production_checks(workspace: Path, tech_stack: str) -> None:
    """Same completeness check the workflow uses before dev."""
    db = next(workspace.glob("tasks_*.db"), None)
    project_id = workspace.name.replace("job-", "") if db else "test-architect"
    db_path = db or workspace / f"tasks_{project_id}.db"
    tm = TaskManager(str(db_path), project_id)
    result = tm.validate_tech_stack_completeness(
        tech_stack,
        design_spec=(workspace / "design_spec.md").read_text(encoding="utf-8", errors="replace")
        if (workspace / "design_spec.md").exists()
        else "",
        solution_spec=(workspace / "solution_spec.md").read_text(encoding="utf-8", errors="replace")
        if (workspace / "solution_spec.md").exists()
        else "",
    )
    if not result.get("valid"):
        print("\n❌ validate_tech_stack_completeness FAILED (production gate):")
        for issue in result.get("issues", []):
            print(f"  - {issue}")
        sys.exit(1)
    entries = tm._extract_files_with_descriptions(tech_stack)
    src = [e["path"] for e in entries if tm._is_source_file_path(e["path"], e.get("description", ""))]
    print(f"✅ Production validator OK — {len(src)} source file(s) parsed from tree")


def _resolve_job_id() -> str:
    return os.environ.get("JOB_ID", "").strip()


def main() -> None:
    workspace = _resolve_workspace()
    job_id = _resolve_job_id()
    fresh = os.environ.get("FRESH", "").lower() in ("1", "true", "yes")

    if fresh and workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True, exist_ok=True)

    vision, design_spec, user_stories, approved, solution_spec = _load_inputs(workspace)

    print("=" * 72)
    print("TECH ARCHITECT ONLY (isolated)")
    print("=" * 72)
    print(f"Workspace: {workspace}")
    print(f"Approved solution contract: {approved}")
    print(f"Vision: {vision[:100]}...")
    print(f"design_spec: {len(design_spec)} chars")

    # Remove prior architect outputs so re-runs are clean
    for name in ("tech_stack.md", "implementation_plan.md"):
        p = workspace / name
        if p.exists():
            p.unlink()

    from llamaindex_crew.config import ConfigLoader
    from llamaindex_crew.utils.llm_config import ensure_llm_api_key, user_llm_context
    from job_database import JobDatabase

    db_path = Path(os.environ.get("JOB_DB_PATH", "/app/data/crew_jobs.db"))
    job_db = JobDatabase(db_path)
    fallback = ConfigLoader.load()

    def _run_architect() -> None:
        agent = TechArchitectAgent(workspace_path=workspace)
        agent.define_tech_stack(
            design_spec,
            vision,
            context_digest=os.environ.get("CONTEXT", "Isolated tech architect test"),
            user_stories=user_stories,
            approved_solution=approved,
            solution_spec=solution_spec if approved else None,
        )

    if job_id:
        with user_llm_context(job_id, job_db, fallback) as active_config:
            ensure_llm_api_key(active_config)
            llm = active_config.llm
            print(
                f"LLM: {llm.api_base_url} "
                f"(manager={llm.model_manager}, worker={llm.model_worker}, reviewer={llm.model_reviewer})"
            )
            _run_architect()
    else:
        ensure_llm_api_key(fallback)
        llm = fallback.llm
        print(
            f"LLM: {llm.api_base_url} "
            f"(manager={llm.model_manager}, worker={llm.model_worker}, reviewer={llm.model_reviewer})"
        )
        _run_architect()

    print("\n=== FILES CREATED ===")
    for path in sorted(p for p in workspace.rglob("*") if p.is_file()):
        if path.name.startswith(("tasks_", "state_", "index_")) or path.suffix == ".db":
            continue
        print(f"  {path.relative_to(workspace)} ({path.stat().st_size} B)")

    print("\n=== ARTIFACT ASSERTIONS ===")
    assert_or_exit(validate_tech_stack(workspace), "Tech Architect")

    tech_stack = (workspace / "tech_stack.md").read_text(encoding="utf-8", errors="replace")
    _validate_production_checks(workspace, tech_stack)

    print("\n=== TECH STACK PREVIEW ===")
    print(tech_stack[:2000])
    if len(tech_stack) > 2000:
        print(f"\n... ({len(tech_stack) - 2000} more chars)")

    print("\n✅ TECH ARCHITECT ONLY — PASSED")


if __name__ == "__main__":
    main()
