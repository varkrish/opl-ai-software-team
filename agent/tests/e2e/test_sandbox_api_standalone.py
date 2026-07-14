"""
Standalone Sandbox API E2E — runs via run_build_pipeline (no container, no HTTP server).

Same vision as mono scripts/e2e-sandbox-api-vision.json and submit-e2e-sandbox-api-job.sh,
but invokes the build pipeline in-process with a temp DB/workspace.

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
from llamaindex_crew.utils.output_parser import (
    is_agent_planning_monologue,
    is_llm_stub_content,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "sandbox_api_vision.json"


def _load_sandbox_fixture() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _assert_go_sources_clean(workspace: Path) -> None:
    """Reject channel stubs and planning monologues written as .go source."""
    go_files = list(workspace.rglob("*.go"))
    assert go_files, "expected at least one Go source file"
    corrupt: list[str] = []
    for path in go_files:
        text = path.read_text(encoding="utf-8", errors="replace")
        if is_llm_stub_content(text) or is_agent_planning_monologue(text):
            corrupt.append(str(path.relative_to(workspace)))
    assert not corrupt, f"corrupt Go files: {corrupt}"


def _assert_sandbox_artifacts(workspace: Path) -> None:
    assert (workspace / "go.mod").is_file(), "missing go.mod"
    assert (workspace / "README.md").is_file(), "missing README.md"
    go_sources = [
        p for p in workspace.rglob("*.go")
        if not p.name.endswith("_test.go")
    ]
    assert len(go_sources) >= 2, (
        f"expected multiple Go packages, found: "
        f"{[str(p.relative_to(workspace)) for p in go_sources]}"
    )


@pytest.mark.e2e
@pytest.mark.slow
@pytest.mark.requires_api_key
@pytest.mark.timeout(1800)
def test_sandbox_api_standalone_e2e(tmp_path, monkeypatch):
    """Fast-path Sandbox API build in-process (no dev container on :8099)."""
    monkeypatch.setenv("SKIP_DELIVERY_MODE_TRIAGE", "1")
    monkeypatch.setenv("AUTH_ENABLED", "false")

    from crew_studio.build_runner import run_build_pipeline
    from crew_studio.job_database import JobDatabase

    fixture = _load_sandbox_fixture()
    vision = fixture["vision"]
    cap = (fixture.get("capability_profile") or "fast").strip().lower()

    db_path = tmp_path / "sandbox_e2e_jobs.db"
    job_db = JobDatabase(db_path)

    job_id = str(uuid.uuid4())
    workspace = tmp_path / "workspace" / f"job-{job_id}"
    workspace.mkdir(parents=True)

    meta = {
        "capability_profile": {"solutioning_path": cap, "source": "e2e"},
        "auto_approve_plan": bool(fixture.get("auto_approve_plan", True)),
        "skip_delivery_mode_guard": True,
    }
    job_db.create_job(job_id, vision, str(workspace), metadata=json.dumps(meta))
    job_db.update_job(job_id, {"status": "queued", "current_phase": "meta"})

    config = ConfigLoader.load()
    progress_log: list[tuple] = []

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

    status = results.get("status")
    assert status in ("completed", "partially_completed"), results

    _assert_sandbox_artifacts(workspace)
    _assert_go_sources_clean(workspace)

    # Assert clean execution log exists and is populated
    execution_log = workspace / "execution.log"
    assert execution_log.is_file(), "missing execution.log"
    log_content = execution_log.read_text(encoding="utf-8")
    assert "=== [LLM Prompt] ===" in log_content or "=== [LLM Response] ===" in log_content
    assert "[code block truncated]" in log_content or "[code truncated]" in log_content

    task_validation = results.get("task_validation") or {}
    if status == "completed":
        assert task_validation.get("valid") is True, task_validation

    validation = results.get("validation_report") or {}
    assert validation.get("overall") in ("PASS", "ISSUES_FOUND", None), validation.get("overall")

    assert any(p[0] == "development" for p in progress_log), (
        "expected development phase in progress log"
    )
