"""Missing Java types must be scaffolded so the per-file fix loop can fill them in.

Java's one-public-type-per-file rule means N types need N files, and the code
generator routinely references types it never wrote files for (TaskDto, Status,
Priority).  javac reports the error against the *referencing* file, so the fix
loop tells DevAgent "fix TaskService.java" — but DevAgent, editing only that
file, cannot conjure a missing class into existence.

The fix: ``_auto_fix_issues`` already creates ``__init__.py`` files during
remediation; this adds the same treatment for Java.  It parses the javac
"cannot find symbol" multi-line block, derives the target path from the
referencing file's directory, and writes a minimal stub the dev agent can then
flesh out.

The dangerous direction is a FALSE NEGATIVE: failing to scaffold a type the
compiler clearly asked for wastes the remaining iterations.  The safe direction
is overly aggressive scaffolding, because the dev agent will overwrite the stub
anyway — so the parser errs toward detecting types rather than suppressing them.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow as W


# ── javac output fixtures ────────────────────────────────────────────────────

SINGLE_MISSING_TYPE = (
    "[ERROR] /app/src/main/java/com/example/task/TaskService.java:[33,28] cannot find symbol\n"
    "  symbol:   class TaskDto\n"
    "  location: class com.example.task.TaskService\n"
)

MULTIPLE_MISSING_TYPES = (
    "[ERROR] /app/src/main/java/com/example/task/TaskService.java:[33,28] cannot find symbol\n"
    "  symbol:   class TaskDto\n"
    "  location: class com.example.task.TaskService\n"
    "[ERROR] /app/src/main/java/com/example/task/TaskController.java:[15,10] cannot find symbol\n"
    "  symbol:   class Status\n"
    "  location: class com.example.task.TaskController\n"
    "[ERROR] /app/src/main/java/com/example/task/TaskService.java:[40,5] cannot find symbol\n"
    "  symbol:   class Priority\n"
    "  location: class com.example.task.TaskService\n"
)

# Maven build log noise that precedes and follows actual errors
REAL_BUILD_LOG = (
    "[INFO] Scanning for projects...\n"
    "[INFO] Building task-api 1.0\n"
    "[INFO] --- maven-compiler-plugin:3.11.0:compile (default-compile) @ task-api ---\n"
    "[ERROR] /app/src/main/java/com/example/task/TaskService.java:[33,28] cannot find symbol\n"
    "  symbol:   class TaskDto\n"
    "  location: class com.example.task.TaskService\n"
    "[ERROR] /app/src/main/java/com/example/task/TaskService.java:[45,12] incompatible types: String cannot be converted to int\n"
    "[INFO] BUILD FAILURE\n"
)

# Method symbols — javac also says "cannot find symbol" for missing methods,
# but scaffolding a file won't fix those.
METHOD_SYMBOL = (
    "[ERROR] /app/src/main/java/com/example/task/TaskService.java:[50,10] cannot find symbol\n"
    "  symbol:   method doStuff()\n"
    "  location: class com.example.task.TaskService\n"
)

VARIABLE_SYMBOL = (
    "[ERROR] /app/src/main/java/com/example/task/TaskService.java:[52,5] cannot find symbol\n"
    "  symbol:   variable myVar\n"
    "  location: class com.example.task.TaskService\n"
)

# Workspace-relative path (no /app prefix), as some sandbox mounts produce
RELATIVE_PATH_ERROR = (
    "[ERROR] src/main/java/com/example/task/TaskService.java:[33,28] cannot find symbol\n"
    "  symbol:   class TaskDto\n"
    "  location: class com.example.task.TaskService\n"
)


# ── parser tests ─────────────────────────────────────────────────────────────

class TestParseJavacMissingSymbols:
    """Parse the multi-line javac 'cannot find symbol' block."""

    def test_single_missing_type(self):
        results = W._parse_javac_missing_symbols(SINGLE_MISSING_TYPE)
        assert len(results) == 1
        assert results[0]["type_name"] == "TaskDto"
        assert "TaskService.java" in results[0]["referencing_file"]

    def test_multiple_missing_types(self):
        results = W._parse_javac_missing_symbols(MULTIPLE_MISSING_TYPES)
        names = {r["type_name"] for r in results}
        assert names == {"TaskDto", "Status", "Priority"}

    def test_ignores_incompatible_types_error(self):
        """Only 'cannot find symbol' with 'symbol: class' triggers scaffolding."""
        results = W._parse_javac_missing_symbols(REAL_BUILD_LOG)
        assert len(results) == 1
        assert results[0]["type_name"] == "TaskDto"

    def test_ignores_method_symbol(self):
        """A missing method cannot be fixed by scaffolding a new file."""
        assert W._parse_javac_missing_symbols(METHOD_SYMBOL) == []

    def test_ignores_variable_symbol(self):
        assert W._parse_javac_missing_symbols(VARIABLE_SYMBOL) == []

    def test_handles_relative_paths(self):
        results = W._parse_javac_missing_symbols(RELATIVE_PATH_ERROR)
        assert len(results) == 1
        assert "TaskService.java" in results[0]["referencing_file"]

    def test_deduplicates_same_type(self):
        """Two errors referencing the same missing type should yield one entry."""
        doubled = SINGLE_MISSING_TYPE + SINGLE_MISSING_TYPE
        results = W._parse_javac_missing_symbols(doubled)
        assert len(results) == 1

    def test_empty_input(self):
        assert W._parse_javac_missing_symbols("") == []

    def test_no_javac_content(self):
        assert W._parse_javac_missing_symbols("Build succeeded!") == []


# ── workspace fixture ────────────────────────────────────────────────────────

@pytest.fixture
def java_workspace(tmp_path):
    """A minimal Java workspace matching the reproduction case."""
    pkg = tmp_path / "src" / "main" / "java" / "com" / "example" / "task"
    pkg.mkdir(parents=True)
    (pkg / "TaskService.java").write_text(
        "package com.example.task;\n\npublic class TaskService {}\n",
        encoding="utf-8",
    )
    (pkg / "TaskController.java").write_text(
        "package com.example.task;\n\npublic class TaskController {}\n",
        encoding="utf-8",
    )
    return tmp_path


def _make_workflow(workspace):
    """Minimal SoftwareDevWorkflow wired for _auto_fix_issues."""
    wf = W.__new__(W)
    wf.workspace_path = workspace
    return wf


# ── scaffolding tests ────────────────────────────────────────────────────────

class TestAutoFixScaffoldsJavaTypes:
    """_auto_fix_issues creates stub .java files for missing types."""

    def _smoke_issue(self, desc):
        """Build an issue dict the way _collect_fixable_issues emits smoke_test issues."""
        return {
            "check": "smoke_test",
            "file": "src/main/java/com/example/task/TaskService.java",
            "description": desc,
        }

    def test_scaffolds_missing_type_file(self, java_workspace):
        """The whole point: a stub file must appear on disk."""
        wf = _make_workflow(java_workspace)
        issues = [self._smoke_issue(
            "This file failed to build/run. Fix the code so the project "
            "compiles and starts cleanly:\n"
            "[ERROR] /app/src/main/java/com/example/task/TaskService.java:[33,28] cannot find symbol\n"
            "  symbol:   class TaskDto\n"
            "  location: class com.example.task.TaskService"
        )]
        fixed = wf._auto_fix_issues(issues)

        target = java_workspace / "src" / "main" / "java" / "com" / "example" / "task" / "TaskDto.java"
        assert target.exists(), "stub file must be created on disk"
        assert any(f["check"] == "missing_java_type" for f in fixed)

    def test_stub_has_correct_package(self, java_workspace):
        """The stub must compile — that requires a correct package declaration."""
        wf = _make_workflow(java_workspace)
        issues = [self._smoke_issue(
            "[ERROR] /app/src/main/java/com/example/task/TaskService.java:[33,28] cannot find symbol\n"
            "  symbol:   class TaskDto\n"
            "  location: class com.example.task.TaskService"
        )]
        wf._auto_fix_issues(issues)

        content = (java_workspace / "src" / "main" / "java" / "com" / "example" / "task" / "TaskDto.java").read_text()
        assert "package com.example.task;" in content

    def test_stub_contains_todo_not_guessed_shape(self, java_workspace):
        """
        javac reports every type as 'class' regardless of whether it's an enum,
        interface, or record.  The stub must carry a TODO with the compiler
        context so DevAgent can decide the real shape, rather than baking in a
        guess that biases the agent wrong.
        """
        wf = _make_workflow(java_workspace)
        issues = [self._smoke_issue(
            "[ERROR] /app/src/main/java/com/example/task/TaskService.java:[33,28] cannot find symbol\n"
            "  symbol:   class Status\n"
            "  location: class com.example.task.TaskService"
        )]
        wf._auto_fix_issues(issues)

        content = (java_workspace / "src" / "main" / "java" / "com" / "example" / "task" / "Status.java").read_text()
        assert "TODO" in content
        assert "class Status" in content  # must still compile

    def test_does_not_overwrite_existing_file(self, java_workspace):
        """Idempotency: if the file already exists, leave it alone."""
        wf = _make_workflow(java_workspace)
        existing = java_workspace / "src" / "main" / "java" / "com" / "example" / "task" / "TaskDto.java"
        existing.write_text("package com.example.task;\npublic class TaskDto { int x; }\n")

        issues = [self._smoke_issue(
            "[ERROR] /app/src/main/java/com/example/task/TaskService.java:[33,28] cannot find symbol\n"
            "  symbol:   class TaskDto\n"
            "  location: class com.example.task.TaskService"
        )]
        fixed = wf._auto_fix_issues(issues)

        assert "int x;" in existing.read_text(), "must not overwrite user content"
        assert not any(f["check"] == "missing_java_type" for f in fixed)

    def test_skips_when_no_java_siblings(self, tmp_path):
        """
        Path derivation relies on the referencing file existing in a Java
        source tree.  If the directory has no .java files at all, something is
        wrong with the path — don't scaffold blindly.
        """
        # Create a workspace with the directory but no .java files in it
        pkg = tmp_path / "src" / "main" / "java" / "com" / "example" / "task"
        pkg.mkdir(parents=True)
        # No .java files — path derivation is suspect

        wf = _make_workflow(tmp_path)
        issues = [{
            "check": "smoke_test",
            "file": "src/main/java/com/example/task/TaskService.java",
            "description": (
                "[ERROR] /app/src/main/java/com/example/task/TaskService.java:[33,28] cannot find symbol\n"
                "  symbol:   class TaskDto\n"
                "  location: class com.example.task.TaskService"
            ),
        }]
        fixed = wf._auto_fix_issues(issues)
        assert not any(f["check"] == "missing_java_type" for f in fixed)

    def test_scaffolds_multiple_types(self, java_workspace):
        """Three missing types from the same package produce three separate files."""
        wf = _make_workflow(java_workspace)
        desc = (
            "[ERROR] /app/src/main/java/com/example/task/TaskService.java:[33,28] cannot find symbol\n"
            "  symbol:   class TaskDto\n"
            "  location: class com.example.task.TaskService\n"
            "[ERROR] /app/src/main/java/com/example/task/TaskController.java:[15,10] cannot find symbol\n"
            "  symbol:   class Status\n"
            "  location: class com.example.task.TaskController\n"
            "[ERROR] /app/src/main/java/com/example/task/TaskService.java:[40,5] cannot find symbol\n"
            "  symbol:   class Priority\n"
            "  location: class com.example.task.TaskService\n"
        )
        issues = [
            {"check": "smoke_test", "file": "src/main/java/com/example/task/TaskService.java",
             "description": desc},
        ]
        fixed = wf._auto_fix_issues(issues)
        scaffolded = {f["file"] for f in fixed if f["check"] == "missing_java_type"}
        pkg = "src/main/java/com/example/task"
        assert scaffolded == {
            f"{pkg}/TaskDto.java",
            f"{pkg}/Status.java",
            f"{pkg}/Priority.java",
        }

    def test_package_derived_from_directory_structure(self, tmp_path):
        """
        The package declaration must match the directory path, not be blindly
        copied from the location line — the referencing file might import from
        a different package.
        """
        pkg = tmp_path / "src" / "main" / "java" / "com" / "myapp" / "dto"
        pkg.mkdir(parents=True)
        (pkg / "Helper.java").write_text(
            "package com.myapp.dto;\npublic class Helper {}\n"
        )

        wf = _make_workflow(tmp_path)
        issues = [{
            "check": "smoke_test",
            "file": "src/main/java/com/myapp/dto/Helper.java",
            "description": (
                "[ERROR] /app/src/main/java/com/myapp/dto/Helper.java:[5,10] cannot find symbol\n"
                "  symbol:   class ResponseDto\n"
                "  location: class com.myapp.dto.Helper"
            ),
        }]
        wf._auto_fix_issues(issues)

        content = (pkg / "ResponseDto.java").read_text()
        assert "package com.myapp.dto;" in content


# ── allowlist integration ────────────────────────────────────────────────────

class TestAllowlistIntegration:
    """
    Scaffolded files must be writable by the manifest guard in the same
    iteration.  This is the claim from the bug report: expand_remediation_paths
    uses rglob on the live workspace, so a file created by _auto_fix_issues is
    on disk before remediation_write_allowlist is computed.
    """

    def test_scaffolded_file_is_in_remediation_allowlist(self, java_workspace):
        from llamaindex_crew.utils.manifest_guard import expand_remediation_paths

        wf = _make_workflow(java_workspace)
        issues = [{
            "check": "smoke_test",
            "file": "src/main/java/com/example/task/TaskService.java",
            "description": (
                "[ERROR] /app/src/main/java/com/example/task/TaskService.java:[33,28] cannot find symbol\n"
                "  symbol:   class TaskDto\n"
                "  location: class com.example.task.TaskService"
            ),
        }]
        wf._auto_fix_issues(issues)

        # Now compute the allowlist — same as _run_post_build_fix_iteration does
        allowed = expand_remediation_paths(set(), java_workspace)
        assert "src/main/java/com/example/task/TaskDto.java" in allowed


# ── end-to-end through the fix loop ─────────────────────────────────────────

class TestFixLoopIntegration:
    """
    After scaffolding, the next validation round attributes errors to the new
    file, and the per-file fix loop dispatches DevAgent to fill it in.
    """

    def test_scaffolded_file_appears_in_fix_dispatch(self, java_workspace):
        """
        Round 1: smoke_test reports 'cannot find symbol: class TaskDto' against
        TaskService.java.  _auto_fix_issues scaffolds TaskDto.java.  The fix
        loop dispatches TaskService.java to DevAgent.

        Round 2: smoke_test now reports errors *in* TaskDto.java (the stub is
        incomplete).  The fix loop dispatches TaskDto.java — which only exists
        because we scaffolded it.
        """
        wf = W.__new__(W)
        wf.workspace_path = java_workspace
        wf.dev_agent = object()
        wf._validation_report = {}
        wf.job_db = None

        fix_calls = []
        round_num = [0]

        smoke_round_1 = (
            "[ERROR] /app/src/main/java/com/example/task/TaskService.java:[33,28] cannot find symbol\n"
            "  symbol:   class TaskDto\n"
            "  location: class com.example.task.TaskService"
        )

        def validation_suite():
            round_num[0] += 1
            if round_num[0] == 1:
                return {
                    "overall": "ISSUES_FOUND",
                    "checks": {
                        "smoke_test": {"pass": False, "result": smoke_round_1},
                    },
                }
            elif round_num[0] == 2:
                # Now TaskDto.java exists — compiler finds it but it's empty
                return {
                    "overall": "ISSUES_FOUND",
                    "checks": {
                        "smoke_test": {
                            "pass": False,
                            "result": (
                                "[ERROR] /app/src/main/java/com/example/task/TaskDto.java:[3,1] "
                                "class TaskDto needs implementation"
                            ),
                        },
                    },
                }
            return {"overall": "PASS", "checks": {}}

        wf._run_validation_suite = validation_suite
        wf._report_progress = lambda *a, **kw: None
        wf._build_wiring_allowlist = lambda: None
        wf._run_post_build_fix_with_context = (
            lambda fp, descs, all_files: fix_calls.append(fp)
        )
        wf.task_manager = type(
            "TM", (), {"get_registered_file_paths": lambda self: set()}
        )()

        wf._run_post_build_fix_iteration()

        # The scaffolded file must appear in the second round's fix dispatch
        assert "src/main/java/com/example/task/TaskDto.java" in fix_calls, (
            "scaffolded file must be dispatched to DevAgent in a subsequent round"
        )
