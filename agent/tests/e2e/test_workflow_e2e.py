"""
Full-path workflow E2E — runs a complete build job via run_build_pipeline (local, no container).

Requires LLM API key in ~/.crew-ai/config.yaml or env (OPENROUTER_API_KEY / OPENAI_API_KEY).
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import pytest

_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_root))
sys.path.insert(0, str(_root / "agent"))
sys.path.insert(0, str(_root / "agent" / "src"))

from llamaindex_crew.config import ConfigLoader
from llamaindex_crew.workflows.workflow_resolver import (
    FALLBACK_PIPELINES,
    flatten_pipeline,
    is_tdd_pipeline,
)


def _parse_meta(job: dict) -> dict:
    meta = job.get("metadata") or {}
    if isinstance(meta, str):
        return json.loads(meta) if meta else {}
    return meta if isinstance(meta, dict) else {}


@pytest.mark.e2e
@pytest.mark.slow
@pytest.mark.timeout(900)
def test_full_path_job_e2e(tmp_path, monkeypatch):
    """Submit and run a full capability_profile build job end-to-end."""
    monkeypatch.setenv("SKIP_DELIVERY_MODE_TRIAGE", "1")
    monkeypatch.setenv("AUTH_ENABLED", "false")

    from crew_studio.build_runner import run_build_pipeline
    from crew_studio.job_database import JobDatabase

    db_path = tmp_path / "e2e_jobs.db"
    job_db = JobDatabase(db_path)

    job_id = str(uuid.uuid4())
    workspace = tmp_path / "workspace" / f"job-{job_id}"
    workspace.mkdir(parents=True)

    vision = (
        "Create a minimal Python hello-world CLI. "
        "One module with a main() that prints 'Hello, world!'. "
        "Include a pytest unit test. No web UI, no database."
    )
    meta = {
        "capability_profile": {"solutioning_path": "full", "source": "e2e"},
        "solution_approved": True,
        "pending_review_approved": True,
        "auto_approve_plan": True,
        "skip_delivery_mode_guard": True,
    }
    job_db.create_job(job_id, vision, str(workspace), metadata=json.dumps(meta))
    job_db.update_job(job_id, {"status": "queued", "current_phase": "meta"})

    # Pre-approved solution spec skips solutioning pause but keeps full pipeline phases.
    (workspace / "solution_spec.md").write_text(
        "# Solution Specification\n\n"
        "## Stack\n"
        "Python 3.11 CLI with a single `main()` printing `Hello, world!`.\n"
        "pytest for unit tests. No database, no cache, no web UI.\n",
        encoding="utf-8",
    )

    config = ConfigLoader.load()
    progress_log: list = []

    def _progress(phase, pct, msg=None):
        progress_log.append((phase, pct, msg))

    results = run_build_pipeline(
        job_id,
        workspace,
        vision,
        config,
        _progress,
        job_db,
        resume=False,
    )

    assert results.get("status") == "completed", results
    assert results.get("task_validation", {}).get("valid") is True

    job = job_db.get_job(job_id)
    assert job is not None

    for artifact in ("user_stories.md", "tech_stack.md"):
        assert (workspace / artifact).is_file(), f"missing {artifact}"

    meta_out = _parse_meta(job)
    pipeline = meta_out.get("selected_workflow_phases") or FALLBACK_PIPELINES["full"]
    flat = flatten_pipeline(pipeline)
    assert "qa" in flat
    assert flat.index("qa") < flat.index("development"), flat

    if is_tdd_pipeline(pipeline):
        assert meta_out.get("qa_phase_completed") is True

    py_files = [p for p in workspace.rglob("*.py") if "test" not in p.name.lower()]
    test_files = list(workspace.rglob("test_*.py")) + list(workspace.rglob("*_test.py"))
    assert py_files, "expected at least one Python source file"
    assert test_files, "expected pytest files on full/TDD path"

    validation = results.get("validation_report") or {}
    assert validation.get("overall") in ("PASS", "ISSUES_FOUND"), validation.get("overall")
