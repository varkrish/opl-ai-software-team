"""
Real-LLM E2E for the full solutioning loop (research → architect → critique).

Unlike test_workflow_e2e.py, this does NOT pre-seed solution_spec.md or set
solution_approved=True — the stack_contract phase runs the live loop and pauses
at pending_solution_review.

Requires LLM API key in ~/.crew-ai/config.yaml or env.
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
from llamaindex_crew.workflows.solutioning_loop import (
    is_critique_approved,
    read_stack_manifest,
)


def _parse_meta(job: dict) -> dict:
    meta = job.get("metadata") or {}
    if isinstance(meta, str):
        return json.loads(meta) if meta else {}
    return meta if isinstance(meta, dict) else {}


def _assert_solutioning_artifacts(workspace: Path) -> dict:
    """Validate research/architect/critique outputs on disk."""
    spec_path = workspace / "solution_spec.md"
    assert spec_path.is_file(), "solution_spec.md missing after solutioning"
    spec_text = spec_path.read_text(encoding="utf-8", errors="replace")
    assert len(spec_text.strip()) > 200, "solution_spec.md too short"

    candidates_path = workspace / "solution_candidates.json"
    assert candidates_path.is_file(), "solution_candidates.json missing"
    candidates = json.loads(candidates_path.read_text(encoding="utf-8"))
    assert isinstance(candidates, list)

    critique_files = sorted(workspace.glob("solution_critique_pass_*.json"))
    spec_pass_files = sorted(workspace.glob("solution_spec_pass_*.md"))
    assert critique_files, "no solution_critique_pass_*.json files"
    assert spec_pass_files, "no solution_spec_pass_*.md files"
    assert len(critique_files) == len(spec_pass_files)

    critiques = []
    for cf in critique_files:
        data = json.loads(cf.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert "approved" in data
        if data.get("must_fix"):
            assert data["approved"] is False, (
                f"{cf.name}: must_fix present but approved=true"
            )
        critiques.append(data)

    manifest = read_stack_manifest(workspace)
    assert manifest is not None, "stack_manifest.json missing"
    assert manifest.get("path") == "full"

    return {
        "pass_count": len(critique_files),
        "critiques": critiques,
        "spec_len": len(spec_text),
        "candidates_count": len(candidates),
    }


def _simulate_solution_approve(job_db, job_id: str, meta: dict) -> None:
    """Mirror POST /api/jobs/{id}/approve for pending_solution_review."""
    from llamaindex_crew.workflows.solutioning_loop import (
        write_stack_manifest_from_solution_spec,
    )
    from llamaindex_crew.utils.vision_stack_analysis import infer_capability_profile

    job = job_db.get_job(job_id)
    workspace = Path(job["workspace_path"])
    meta["solution_approved"] = True
    write_stack_manifest_from_solution_spec(
        job.get("vision", ""),
        infer_capability_profile(job.get("vision", "")),
        workspace,
    )
    job_db.update_job(job_id, {
        "status": "queued",
        "current_phase": "product_owner",
        "metadata": json.dumps(meta),
    })


@pytest.mark.e2e
@pytest.mark.slow
@pytest.mark.timeout(600)
def test_full_path_solutioning_loop_pauses_for_review(tmp_path, monkeypatch):
    """Full path with solution_approved=False runs live solutioning and pauses."""
    monkeypatch.setenv("SKIP_DELIVERY_MODE_TRIAGE", "1")
    monkeypatch.setenv("AUTH_ENABLED", "false")

    from crew_studio.build_runner import run_build_pipeline
    from crew_studio.job_database import JobDatabase

    db_path = tmp_path / "e2e_solutioning.db"
    job_db = JobDatabase(db_path)

    job_id = str(uuid.uuid4())
    workspace = tmp_path / "workspace" / f"job-{job_id}"
    workspace.mkdir(parents=True)

    vision = (
        "Build a minimal Python FastAPI service with one GET /health endpoint "
        "returning {\"status\": \"ok\"}. No database, no frontend, no auth. "
        "Use pytest for tests."
    )
    meta = {
        "capability_profile": {"solutioning_path": "full", "source": "e2e"},
        "skip_delivery_mode_guard": True,
    }
    job_db.create_job(job_id, vision, str(workspace), metadata=json.dumps(meta))
    job_db.update_job(job_id, {"status": "queued", "current_phase": "meta"})

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

    assert results.get("status") == "pending_solution_review", results

    job = job_db.get_job(job_id)
    assert job is not None
    assert job["status"] == "pending_solution_review"
    assert job["current_phase"] == "pending_solution_review"

    meta_out = _parse_meta(job)
    assert meta_out.get("solution_pending_review") is True
    assert meta_out.get("solution_approved") is not True

    artifact_info = _assert_solutioning_artifacts(workspace)
    assert artifact_info["pass_count"] >= 1

    if meta_out.get("solution_pass_count") is not None:
        assert meta_out["solution_pass_count"] == artifact_info["pass_count"]
        assert meta_out.get("solution_max_passes", 0) >= 1

    phases = [p[0] for p in progress_log]
    assert "solutioning" in phases

    last_critique = artifact_info["critiques"][-1]
    if is_critique_approved(last_critique):
        assert meta_out.get("solution_approved_by_critique") is True
    else:
        assert meta_out.get("solution_approved_by_critique") is False


@pytest.mark.e2e
@pytest.mark.slow
@pytest.mark.timeout(900)
def test_full_path_solutioning_approve_then_build(tmp_path, monkeypatch):
    """Solutioning pause → human approve → resume through full pipeline."""
    monkeypatch.setenv("SKIP_DELIVERY_MODE_TRIAGE", "1")
    monkeypatch.setenv("AUTH_ENABLED", "false")

    from crew_studio.build_runner import run_build_pipeline
    from crew_studio.job_database import JobDatabase

    db_path = tmp_path / "e2e_solutioning_full.db"
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
        "skip_delivery_mode_guard": True,
        "auto_approve_plan": True,
    }
    job_db.create_job(job_id, vision, str(workspace), metadata=json.dumps(meta))
    job_db.update_job(job_id, {"status": "queued", "current_phase": "meta"})

    config = ConfigLoader.load()

    pause_results = run_build_pipeline(
        job_id, workspace, vision, config, lambda *a: None, job_db, resume=False,
    )
    assert pause_results.get("status") == "pending_solution_review", pause_results
    _assert_solutioning_artifacts(workspace)

    meta = _parse_meta(job_db.get_job(job_id))
    meta["pending_review_approved"] = True
    _simulate_solution_approve(job_db, job_id, meta)

    results = run_build_pipeline(
        job_id, workspace, vision, config, lambda *a: None, job_db, resume=True,
    )

    assert results.get("status") == "completed", results
    assert results.get("task_validation", {}).get("valid") is True

    meta_out = _parse_meta(job_db.get_job(job_id))
    assert meta_out.get("solution_approved") is True

    for artifact in ("user_stories.md", "tech_stack.md"):
        assert (workspace / artifact).is_file(), f"missing {artifact}"

    test_files = list(workspace.rglob("test_*.py"))
    assert test_files, "expected pytest files after full build"
