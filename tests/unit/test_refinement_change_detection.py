"""Refinement must not report success when nothing was edited.

Observed live on 2026-08-08: a file-scoped refinement was marked ``completed``
in 8 seconds while ``calculator.py`` stayed byte-identical. The agent had fallen
back to ReAct mode and emitted commentary instead of calling a tool, so it wrote
nothing — but ``_workspace_has_changes()`` still returned True because the
agent's own tool logging appends to ``execution.log`` on every invocation.
A dirty tree was being treated as evidence of an edit.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from crew_studio.refinement_runner import (  # noqa: E402
    _is_platform_artifact,
    _workspace_has_changes,
)

git = pytest.importorskip("git")


@pytest.mark.parametrize(
    "path",
    [
        "execution.log",
        "crew_errors.log",
        "validation_report.json",
        "wiring_contract.json",
        "state_abc-123.json",
        "tasks_abc-123.db",
        "nested/dir/execution.log",
    ],
)
def test_platform_artifacts_are_not_edits(path):
    assert _is_platform_artifact(path) is True


@pytest.mark.parametrize("path", ["calculator.py", "src/app.js", "README.md"])
def test_source_files_are_edits(path):
    assert _is_platform_artifact(path) is False


def _init_repo(tmp_path):
    repo = git.Repo.init(tmp_path)
    (tmp_path / "calculator.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    repo.git.add(A=True)
    repo.index.commit("initial")
    return repo


def test_agent_log_churn_alone_is_not_a_change(tmp_path):
    """The exact live failure: only execution.log changed → not an edit."""
    _init_repo(tmp_path)
    (tmp_path / "execution.log").write_text("tool call: file_reader\n", encoding="utf-8")
    (tmp_path / "tasks_job-1.db").write_bytes(b"\x00\x01")

    assert _workspace_has_changes(tmp_path) is False


def test_real_source_edit_is_detected(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "execution.log").write_text("noise\n", encoding="utf-8")
    (tmp_path / "calculator.py").write_text(
        "def add(a, b):\n    return a + b\n\ndef power(a, b):\n    return a ** b\n",
        encoding="utf-8",
    )

    assert _workspace_has_changes(tmp_path) is True


def test_new_source_file_is_detected(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "helper.py").write_text("X = 1\n", encoding="utf-8")

    assert _workspace_has_changes(tmp_path) is True
