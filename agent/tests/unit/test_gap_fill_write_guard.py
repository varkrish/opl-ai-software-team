"""
Post-development gap-fill must not be allowed to invent a parallel project.

``_run_development_phase`` disables the write guard entirely before its
"post-dev gap-fill" step, then asks DevAgent to fix entrypoint wiring and
structural gaps. With no allowlist in force, the agent answers "something is
structurally missing" by creating the layout it would have chosen itself —
beside the one that already exists.

Seen live, and in both languages, so this is not a path-convention problem:

  Python job 76f2138d — dev phase created app/{models,schemas,service,router,
    main}.py; gap-fill then added api/, core/, db/, models/, schemas/,
    services/ and a second test module. compileall passed both trees, so the
    job was graded healthy while carrying an unwired duplicate project.

  Java job d32dcaf7 — dev phase created src/main/java/com/example/task/*.java;
    gap-fill then added model/, controller/, service/, repository/, util/ at
    the workspace root, where Maven cannot see them at all.

The guard already exists and works (file_writer logs "REJECTED … not in the
registered task list"). It was simply switched off for this step. These tests
pin that it stays on, and that legitimate gap-fill still works.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from llamaindex_crew.tools import file_tools  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_guard():
    file_tools.set_allowed_file_paths(None)
    yield
    file_tools.set_allowed_file_paths(None)


def test_guard_rejects_a_brand_new_parallel_tree(tmp_path):
    """The exact shape of the live failure: a new top-level package."""
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "service.py").write_text("x = 1", encoding="utf-8")

    file_tools.set_allowed_file_paths({"app/service.py"}, workspace=str(tmp_path))
    result = file_tools.file_writer(
        "services/__init__.py", "", workspace_path=str(tmp_path)
    )

    assert "Rejected" in result or "❌" in result
    assert not (tmp_path / "services").exists(), "a parallel tree must not be created"


def test_guard_allows_editing_a_registered_file(tmp_path):
    (tmp_path / "app").mkdir()
    target = tmp_path / "app" / "service.py"
    target.write_text("old", encoding="utf-8")

    file_tools.set_allowed_file_paths({"app/service.py"}, workspace=str(tmp_path))
    file_tools.file_writer("app/service.py", "new", workspace_path=str(tmp_path))

    assert target.read_text(encoding="utf-8").strip() == "new"


def test_guard_off_permits_anything(tmp_path):
    """Sanity check on the mechanism itself — None really does disable it."""
    file_tools.set_allowed_file_paths(None, workspace=str(tmp_path))
    file_tools.file_writer("anywhere/x.py", "y = 1", workspace_path=str(tmp_path))
    assert (tmp_path / "anywhere" / "x.py").is_file()


# ── the workflow must keep the guard on during gap-fill ─────────────────────

class _RecordingDevAgent:
    """Captures whether a guard was in force when the agent was invoked."""

    def __init__(self, workspace):
        self.workspace = str(workspace)
        self.guard_active_during_run = None
        self.calls = 0

    def run(self, *args, **kwargs):
        self.calls += 1
        self.guard_active_during_run = (
            file_tools._allowed_paths_by_workspace.get(self.workspace) is not None
        )
        return "ok"


def _workflow_for_gap_fill(tmp_path, structure_gaps):
    from llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow as W

    wf = W.__new__(W)
    wf.workspace_path = tmp_path
    wf.tech_stack = "python fastapi"
    wf.user_stories = ""
    wf.dev_agent = _RecordingDevAgent(tmp_path)
    wf.task_manager = type(
        "TM", (), {
            "detect_workspace_structure_gaps": lambda self, ws: list(structure_gaps),
            "get_registered_file_paths": lambda self: {"app/service.py"},
        },
    )()
    return wf


def test_gap_fill_runs_with_the_guard_in_force(tmp_path):
    """
    The regression. Before the fix the workflow called
    set_allowed_file_paths(None) here, so DevAgent could write anywhere.
    """
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "service.py").write_text("x = 1", encoding="utf-8")
    wf = _workflow_for_gap_fill(tmp_path, ["a module is missing"])

    wf._run_post_dev_gap_fill()

    assert wf.dev_agent.calls > 0, "gap-fill should still run"
    assert wf.dev_agent.guard_active_during_run is True, (
        "DevAgent must not be able to write outside the registered manifest"
    )


def test_gap_fill_clears_the_guard_afterwards(tmp_path):
    """Leaving it set would silently constrain every later phase."""
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "service.py").write_text("x = 1", encoding="utf-8")
    wf = _workflow_for_gap_fill(tmp_path, ["gap"])

    wf._run_post_dev_gap_fill()

    assert file_tools._allowed_paths_by_workspace.get(str(tmp_path)) is None


def test_no_gaps_means_no_agent_call(tmp_path, monkeypatch):
    """
    Nothing to fix → no LLM call. The entrypoint validator is stubbed rather
    than satisfied: this test is about the gap-fill trigger logic, not about
    reproducing whatever the validator currently expects of a real project.
    """
    from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

    monkeypatch.setattr(
        CodeCompletenessValidator, "validate_entrypoint",
        staticmethod(lambda *a, **k: {"valid": True}),
    )
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "service.py").write_text("x = 1", encoding="utf-8")

    wf = _workflow_for_gap_fill(tmp_path, [])
    wf._run_post_dev_gap_fill()
    assert wf.dev_agent.calls == 0


def test_gap_fill_failure_does_not_raise(tmp_path):
    wf = _workflow_for_gap_fill(tmp_path, ["gap"])

    class Boom:
        def run(self, *a, **k):
            raise RuntimeError("agent down")

    wf.dev_agent = Boom()
    wf._run_post_dev_gap_fill()  # must not propagate
