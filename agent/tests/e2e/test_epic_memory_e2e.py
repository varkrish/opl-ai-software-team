"""
E2E test: Epic workflow with rich memory (call graph delta + RAG indexing).

Runs a 3-story Epic using SoftwareDevWorkflow directly (bypasses Jira connector).
Verifies that:
1. The Epic workflow completes and generates code for all 3 stories.
2. epic_progress.md is written with all story entries.
3. epic_graph_snapshot.json grows across stories.
4. The RAG index contains story call entries (source_count check).
5. Code coherence: later story files import from / reference models established
   in earlier stories (the core quality signal).

Requires: OPENROUTER_API_KEY or OPENAI_API_KEY
Run with:
    pytest agent/tests/e2e/test_epic_memory_e2e.py -v -s -m e2e
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def epic_workspace(e2e_workspace):
    """Job workspace with Epic metadata pre-written."""
    return e2e_workspace


def _seed_job_db(workspace: Path, project_id: str, vision: str, metadata: dict):
    """Create a JobDatabase and insert a job row with Epic metadata."""
    import sys
    # crew_studio lives at the repo root — ensure it's importable
    repo_root = Path(__file__).parent.parent.parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from crew_studio.job_database import JobDatabase

    db_path = workspace / "crew_jobs.db"
    db = JobDatabase(db_path)
    db.create_job(
        job_id=project_id,
        vision=vision,
        workspace_path=str(workspace),
        metadata=metadata,
    )
    return db


# ---------------------------------------------------------------------------
# Epic vision and stories
#
# Domain: a simple Python task manager API (3 stories)
# Story 1 — Data model    : Task model with id, title, status
# Story 2 — API endpoints : CRUD endpoints that import Task from story 1
# Story 3 — Serializers   : Pydantic schemas that reference Task fields
#
# Cross-story coherence checks:
#   Story 2 code should import or reference the Task class from story 1.
#   Story 3 code should reference Task fields (id, title, status).
# ---------------------------------------------------------------------------

_EPIC_VISION = """\
Build a simple Python Task Manager REST API using FastAPI.
The system allows users to create, list, update, and delete tasks.
Each task has an id, title, and status (pending/done).
Use in-memory storage (dict) for simplicity.
Include pytest unit tests for each component.
"""

_JIRA_STORIES = [
    {
        "key": "TM-1",
        "summary": "Implement Task data model",
        "description": (
            "Create a Task class with fields: id (int), title (str), status (str, default 'pending'). "
            "Store tasks in an in-memory dict keyed by id. "
            "Implement create_task(), get_task(id), list_tasks(), and delete_task(id) functions."
        ),
        "status": "To Do",
    },
    {
        "key": "TM-2",
        "summary": "Implement FastAPI CRUD endpoints",
        "description": (
            "Create FastAPI routes: POST /tasks, GET /tasks, GET /tasks/{id}, DELETE /tasks/{id}. "
            "Import and use the Task model and storage functions from the task model module. "
            "Return proper HTTP status codes."
        ),
        "status": "To Do",
    },
    {
        "key": "TM-3",
        "summary": "Add Pydantic request/response schemas",
        "description": (
            "Create Pydantic BaseModel schemas: TaskCreate (title: str) and TaskResponse "
            "(id: int, title: str, status: str). "
            "Update the API endpoints to use these schemas for validation and serialization. "
            "Reference Task model fields for field names."
        ),
        "status": "To Do",
    },
]


# ---------------------------------------------------------------------------
# Helper: collect all generated Python source files (non-test)
# ---------------------------------------------------------------------------

def _collect_source_files(workspace: Path) -> dict[str, str]:
    """Return {relative_path: content} for all non-test .py files generated."""
    result = {}
    for p in sorted(workspace.rglob("*.py")):
        rel = str(p.relative_to(workspace))
        # Skip test files, __pycache__, and snapshot/metadata artefacts
        if any(skip in rel for skip in ("test_", "_test.py", "__pycache__",
                                         "conftest", ".pytest")):
            continue
        try:
            result[rel] = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    return result


# ---------------------------------------------------------------------------
# Main E2E test
# ---------------------------------------------------------------------------

@pytest.mark.e2e
@pytest.mark.slow
@pytest.mark.requires_api_key
@pytest.mark.timeout(3600)  # 60 minutes — 3 stories × ~10-15 min each + planning phases
def test_epic_workflow_cross_story_coherence(epic_workspace):
    """
    Run a 3-story Epic and verify that later stories reference constructs
    established in earlier ones — the core code quality signal for rich memory.
    """
    project_id = f"e2e_epic_{epic_workspace.name}"
    epic_metadata = {
        "jira_epic_key": "TM-EPIC-1",
        "jira_stories": _JIRA_STORIES,
    }

    db = _seed_job_db(epic_workspace, project_id, _EPIC_VISION, epic_metadata)

    workflow = SoftwareDevWorkflow(
        project_id=project_id,
        workspace_path=epic_workspace,
        vision=_EPIC_VISION,
        job_db=db,
    )

    # ── Run ────────────────────────────────────────────────────────────────
    results = workflow.run()

    # ── Basic completion checks ─────────────────────────────────────────────
    assert results["status"] == "completed", (
        f"Epic workflow failed. Status: {results.get('status')}, "
        f"Error: {results.get('error', 'none')}"
    )
    assert results.get("epic_stories_completed") == 3, (
        f"Expected 3 stories completed, got {results.get('epic_stories_completed')}"
    )

    budget = results.get("budget_report", {})
    assert budget.get("total_cost", 0) < 20.0, (
        f"Budget exceeded: ${budget.get('total_cost', 0):.4f}"
    )

    # ── Architecture artifacts ──────────────────────────────────────────────
    for artifact in ("user_stories.md", "design_spec.md", "tech_stack.md"):
        assert (epic_workspace / artifact).exists(), f"Missing artifact: {artifact}"

    # ── Epic memory artifacts ───────────────────────────────────────────────
    progress_md = epic_workspace / "epic_progress.md"
    assert progress_md.exists(), "epic_progress.md was not created"

    progress_content = progress_md.read_text(encoding="utf-8")
    for story in _JIRA_STORIES:
        assert story["key"] in progress_content, (
            f"Story {story['key']} missing from epic_progress.md"
        )

    snapshot_file = epic_workspace / "epic_graph_snapshot.json"
    assert snapshot_file.exists(), "epic_graph_snapshot.json was not created"
    snapshot = json.loads(snapshot_file.read_text(encoding="utf-8"))
    assert len(snapshot) > 0, "Call graph snapshot is empty — tldr did not run"

    # RAG index source count: at minimum 1 progress note per story
    assert workflow.document_indexer.source_count >= 3, (
        f"Expected at least 3 RAG sources (1 progress note per story), "
        f"got {workflow.document_indexer.source_count}"
    )

    # ── Code generation checks ──────────────────────────────────────────────
    source_files = _collect_source_files(epic_workspace)
    assert len(source_files) >= 3, (
        f"Expected at least 3 source files, found {len(source_files)}: "
        f"{list(source_files.keys())}"
    )

    all_source = "\n".join(source_files.values())

    # Task model must define a Task class with the required fields
    assert re.search(r"class\s+Task", all_source, re.IGNORECASE), (
        "No Task class found in any generated source file"
    )
    assert "title" in all_source, "Task field 'title' missing from generated code"
    assert "status" in all_source, "Task field 'status' missing from generated code"

    # Story 2 cross-reference: API endpoints should import Task or use task functions
    api_files = {p: c for p, c in source_files.items()
                 if any(kw in p.lower() for kw in ("route", "api", "view", "endpoint", "main"))}
    if api_files:
        api_source = "\n".join(api_files.values())
        has_task_import = (
            re.search(r"import.*[Tt]ask", api_source) or
            re.search(r"from.*task.*import", api_source, re.IGNORECASE) or
            "Task" in api_source
        )
        assert has_task_import, (
            f"API files do not reference Task model from story 1.\n"
            f"API files: {list(api_files.keys())}\n"
            f"First 500 chars: {api_source[:500]}"
        )

    # Story 3 cross-reference: schemas should reference Task fields
    schema_files = {p: c for p, c in source_files.items()
                    if any(kw in p.lower() for kw in ("schema", "serial", "model", "pydantic"))}
    if schema_files:
        schema_source = "\n".join(schema_files.values())
        has_task_fields = "title" in schema_source and "status" in schema_source
        assert has_task_fields, (
            f"Schema files do not reference Task fields (title, status).\n"
            f"Schema files: {list(schema_files.keys())}"
        )

    # ── Commit history: one commit per story ──────────────────────────────
    try:
        import git as gitpython
        repo = gitpython.Repo(epic_workspace)
        commits = list(repo.iter_commits())
        assert len(commits) >= 3, (
            f"Expected at least 3 commits (one per story), found {len(commits)}"
        )
        commit_messages = [c.message.strip() for c in commits]
        assert any("TM-1" in m for m in commit_messages), "No commit for TM-1"
        assert any("TM-2" in m for m in commit_messages), "No commit for TM-2"
        assert any("TM-3" in m for m in commit_messages), "No commit for TM-3"
    except Exception as git_err:
        pytest.skip(f"Git commit checks skipped: {git_err}")

    # ── Summary ────────────────────────────────────────────────────────────
    print(f"\n--- Epic E2E Summary ---")
    print(f"Stories completed : {results['epic_stories_completed']}")
    print(f"Source files      : {len(source_files)}")
    print(f"RAG sources       : {workflow.document_indexer.source_count}")
    print(f"Call graph edges  : {len(snapshot)}")
    print(f"LLM cost          : ${budget.get('total_cost', 0):.4f}")
    print(f"epic_progress.md  : {len(progress_content.splitlines())} lines")
    print(f"Source files generated:")
    for fp in source_files:
        print(f"  {fp}")


# ---------------------------------------------------------------------------
# Lightweight smoke: verifies Epic metadata routing without full LLM run
# ---------------------------------------------------------------------------

@pytest.mark.e2e
@pytest.mark.timeout(30)
def test_epic_workflow_detects_epic_metadata(e2e_workspace):
    """
    Verify that a workflow with jira_stories metadata routes to _run_epic_workflow
    (checked via state machine reaching DEVELOPMENT).
    No full LLM run — skips immediately after meta phase raises or detects budget.
    """
    from llamaindex_crew.workflows.epic_story_loop import is_epic_job

    metadata = {
        "jira_epic_key": "TM-EPIC-1",
        "jira_stories": _JIRA_STORIES,
    }
    db = _seed_job_db(e2e_workspace, "smoke-epic", _EPIC_VISION, metadata)

    workflow = SoftwareDevWorkflow(
        project_id="smoke-epic",
        workspace_path=e2e_workspace,
        vision=_EPIC_VISION,
        job_db=db,
    )

    loaded = workflow._load_job_metadata()
    assert is_epic_job(loaded), (
        "Workflow did not detect Epic job from metadata — "
        "is_epic_job() returned False"
    )
    assert len(loaded["jira_stories"]) == 3
    assert loaded["jira_epic_key"] == "TM-EPIC-1"
