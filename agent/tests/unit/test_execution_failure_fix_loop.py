"""Runtime/build failures must reach the per-file fix loop.

Before this wiring, ``smoke_test`` was the only validation check whose failure
never produced a fixable issue: the job was marked ``completed_with_errors`` and
the broken code was never handed back to the dev agent. Attribution matters
because ``_run_post_build_fix_iteration`` groups issues by file and silently
drops any issue with a blank file path.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow as W


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "main.py").write_text("import os\n", encoding="utf-8")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "server.js").write_text("const a = 1\n", encoding="utf-8")
    (tmp_path / "main.go").write_text("package main\n", encoding="utf-8")
    src = tmp_path / "src" / "main" / "java" / "com"
    src.mkdir(parents=True)
    (src / "Foo.java").write_text("class Foo {}\n", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("output,expected", [
    # Python traceback, sandbox mount
    ('  File "/workspace/main.py", line 3, in <module>\n    import nope', "main.py"),
    # Python py_compile, bare relative path
    ('File "main.py", line 3\n    def (\n        ^\nSyntaxError', "main.py"),
    # Go build, ./-prefixed
    ("./main.go:5:2: undefined: fmt.Printl", "main.go"),
    # Node, absolute sandbox path
    ("/workspace/app/server.js:12\nReferenceError: x is not defined", "app/server.js"),
    # Maven
    ("[ERROR] /workspace/src/main/java/com/Foo.java:[12,5] cannot find symbol",
     "src/main/java/com/Foo.java"),
    # Local-container mount prefix
    ('  File "/app/main.py", line 3', "main.py"),
])
def test_extracts_file_from_each_ecosystem(workspace, output, expected):
    assert expected in W._extract_failing_files(output, workspace)


def test_ignores_files_outside_workspace(workspace):
    """Stdlib and dependency frames must never be handed to the agent to edit."""
    output = (
        '  File "/usr/lib/python3.11/json/decoder.py", line 355, in raw_decode\n'
        '  File "/workspace/main.py", line 3, in <module>\n'
        '  File "/workspace/node_modules/express/index.js", line 1\n'
    )
    assert set(W._extract_failing_files(output, workspace)) == {"main.py"}


def test_collects_multiple_messages_per_file(workspace):
    output = (
        "./main.go:5:2: undefined: fmt.Printl\n"
        "./main.go:9:1: missing return\n"
    )
    assert len(W._extract_failing_files(output, workspace)["main.go"]) == 2


def test_message_cap_per_file(workspace):
    """Cap keeps the prompt bounded: 10 matched lines + the trailing error summary."""
    output = "\n".join(f"./main.go:{i}:1: error {i}" for i in range(50))
    assert len(W._extract_failing_files(output, workspace)["main.go"]) == 11


def test_trailing_error_is_included(workspace):
    """The frame names the file; the last line says what actually went wrong."""
    output = (
        '  File "/workspace/main.py", line 3, in <module>\n'
        "    import nope\n"
        "ModuleNotFoundError: No module named 'nope'\n"
    )
    messages = W._extract_failing_files(output, workspace)["main.py"]
    assert any("ModuleNotFoundError" in m for m in messages)


def test_no_attribution_yields_nothing(workspace):
    """A failure naming no real file produces no issue — and must not crash."""
    assert W._extract_failing_files("container exited with code 137", workspace) == {}


def _report(smoke_pass, result=""):
    return {"checks": {"smoke_test": {"pass": smoke_pass, "result": result}}}


def test_failing_smoke_test_becomes_a_fixable_issue(workspace):
    wf = W.__new__(W)
    wf.workspace_path = workspace

    issues = wf._collect_fixable_issues(
        _report(False, '  File "/workspace/main.py", line 3, in <module>\nImportError: no nope')
    )

    smoke_issues = [i for i in issues if i["check"] == "smoke_test"]
    assert len(smoke_issues) == 1
    assert smoke_issues[0]["file"] == "main.py"
    assert "ImportError" in smoke_issues[0]["description"]


def test_passing_smoke_test_yields_no_issue(workspace):
    wf = W.__new__(W)
    wf.workspace_path = workspace
    assert wf._collect_fixable_issues(_report(True)) == []


def test_issue_file_is_non_blank_so_fix_loop_dispatches(workspace):
    """_run_post_build_fix_iteration drops issues with a blank/unknown file."""
    wf = W.__new__(W)
    wf.workspace_path = workspace

    issues = wf._collect_fixable_issues(_report(False, "./main.go:5:2: undefined: x"))

    for issue in issues:
        fp = (issue.get("file") or "").strip()
        assert fp and fp.lower() != "unknown"
