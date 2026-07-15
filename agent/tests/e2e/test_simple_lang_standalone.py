"""
Simple Python / Java greenfield E2E — fast solutioning path only.

Runs via run_build_pipeline (no container, no HTTP server).
Visions are intentionally tiny so we can compare language adapters
and island/codegen quality without the Sandbox API complexity.
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

_FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def _assert_sources_clean(workspace: Path, pattern: str) -> None:
    files = list(workspace.rglob(pattern))
    assert files, f"expected at least one file matching {pattern}"
    corrupt: list[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        if is_llm_stub_content(text) or is_agent_planning_monologue(text):
            corrupt.append(str(path.relative_to(workspace)))
    assert not corrupt, f"corrupt source files: {corrupt}"


def _assert_python_artifacts(workspace: Path) -> None:
    py_files = [
        p for p in workspace.rglob("*.py")
        if p.name != "__init__.py" and "test" not in p.name.lower()
    ]
    assert py_files, "expected Python source files"
    assert any(
        (workspace / name).is_file()
        for name in ("pyproject.toml", "requirements.txt", "setup.py", "README.md")
    ), "expected a Python project marker or README"


def _assert_java_artifacts(workspace: Path) -> None:
    java_files = list(workspace.rglob("*.java"))
    assert java_files, "expected Java source files"
    assert any(
        (workspace / name).is_file()
        for name in ("pom.xml", "build.gradle", "build.gradle.kts", "README.md")
    ), "expected a Java build marker or README"


@pytest.mark.e2e
@pytest.mark.slow
@pytest.mark.requires_api_key
@pytest.mark.timeout(3600)
@pytest.mark.parametrize(
    "fixture_name,assert_artifacts,source_glob",
    [
        ("simple_python_vision.json", _assert_python_artifacts, "*.py"),
        ("simple_java_vision.json", _assert_java_artifacts, "*.java"),
    ],
    ids=["python", "java"],
)
def test_simple_lang_standalone_e2e_fast(
    tmp_path, monkeypatch, fixture_name, assert_artifacts, source_glob
):
    """Fast-path simple calculator builds for Python and Java."""
    import os

    current_path = os.environ.get("PATH", "")
    monkeypatch.setenv("PATH", f"/opt/homebrew/bin:/usr/local/bin:{current_path}")
    monkeypatch.setenv("SKIP_DELIVERY_MODE_TRIAGE", "1")
    monkeypatch.setenv("AUTH_ENABLED", "false")

    from crew_studio.build_runner import run_build_pipeline
    from crew_studio.job_database import JobDatabase

    fixture = _load_fixture(fixture_name)
    vision = fixture["vision"]

    db_path = tmp_path / "simple_lang_e2e_jobs.db"
    job_db = JobDatabase(db_path)

    job_id = str(uuid.uuid4())
    workspace = tmp_path / "workspace" / f"job-{job_id}"
    workspace.mkdir(parents=True)

    meta = {
        "capability_profile": {"solutioning_path": "fast", "source": "e2e"},
        "auto_approve_plan": bool(fixture.get("auto_approve_plan", True)),
        "auto_approve_solution": bool(fixture.get("auto_approve_solution", True)),
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
    if status == "pending_solution_review":
        job_record = job_db.get_job(job_id) or {}
        meta_str = job_record.get("metadata", "{}")
        meta = json.loads(meta_str) if isinstance(meta_str, str) else meta_str
        meta["solution_approved"] = True
        job_db.update_job(job_id, {"metadata": json.dumps(meta), "status": "queued"})

        results = run_build_pipeline(
            job_id,
            workspace,
            vision,
            config,
            _progress,
            job_db,
            resume=True,
        )
        status = results.get("status")

    print(f"\n=== [{fixture_name}] status={status} workspace={workspace} ===")
    print(f"validation={results.get('validation_report')}")
    print(f"files={[str(p.relative_to(workspace)) for p in workspace.rglob('*') if p.is_file()][:40]}")

    assert status in ("completed", "partially_completed", "completed_with_errors"), results

    assert_artifacts(workspace)
    _assert_sources_clean(workspace, source_glob)

    assert any(p[0] == "development" for p in progress_log), (
        "expected development phase in progress log"
    )
