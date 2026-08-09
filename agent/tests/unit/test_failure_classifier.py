"""
Infrastructure failures must not be fed to the code-fix loop.

The loop's only move is "ask DevAgent to rewrite the code", which cannot fix a
container with no network. A real Java job reported "42 issues" that were one
Maven error retried — the classifier exists to stop exactly that.

The dangerous direction is a FALSE POSITIVE: calling a real compile error
"infrastructure" would suppress a bug the loop should have fixed. So the code
side of these tests is the important half — every message a broken program can
realistically produce must stay classified as code.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from llamaindex_crew.utils.failure_classifier import (  # noqa: E402
    classify_failure,
    is_infrastructure_failure,
)


# ── infrastructure: verbatim messages captured from real failing jobs ────────

@pytest.mark.parametrize("output", [
    # Java job 581ae1c6, every remediation iteration
    "[ERROR] Could not create local repository at /home/default/.m2/repository -> [Help 1]",
    "[ERROR] LocalRepositoryNotAccessibleException",
    # Static jobs before the CONTAINER_IMAGES fix
    "❌ No container image configured for project type 'static'",
    # Sandbox plumbing
    "❌ Sandbox API smoke test error: connection reset",
    "❌ SMOKE_TEST_BACKEND=sandbox_api but SANDBOX_API_URL is not set",
    # --net none
    "Could not transfer artifact org.springframework:spring-core:jar:6.1.0 from central",
    "[ERROR] Failed to execute goal: Could not resolve dependencies for project",
    "npm ERR! code ENOTFOUND\nnpm ERR! network request to https://registry.npmjs.org failed",
    "npm ERR! code EAI_AGAIN",
    "dial tcp 142.250.70.17:443: connect: connection refused",
    "java.net.UnknownHostException: repo.maven.apache.org: Name or service not known",
    "curl: (6) Could not resolve host: Temporary failure in name resolution",
    "OSError: [Errno 101] Network is unreachable",
    # read-only root
    "OSError: [Errno 30] Read-only file system: '/home/default/.m2'",
    # missing toolchain — the java_gradle image genuinely has no gradle
    "sh: line 1: gradle: command not found",
])
def test_infrastructure_signatures_are_detected(output):
    result = classify_failure(output)
    assert result.is_infrastructure, f"should be infra: {output[:60]!r}"
    assert result.reason, "an infra classification must explain itself"


# ── code: must NOT be misread as infrastructure ─────────────────────────────

@pytest.mark.parametrize("output", [
    # Java
    "[ERROR] /app/src/main/java/com/example/Task.java:[42,15] cannot find symbol",
    "[ERROR] incompatible types: String cannot be converted to int",
    "[ERROR] class TaskService is public, should be declared in a file named TaskService.java",
    # Python
    "  File \"./app/bad.py\", line 1\n    def bad(: syntax error\nSyntaxError: invalid syntax",
    "ImportError: cannot import name 'Expense' from 'app.models'",
    "AttributeError: 'NoneType' object has no attribute 'id'",
    "E   assert 3 == 4",
    "FAILED tests/test_service.py::test_summary - AssertionError",
    # Node
    "SyntaxError: Unexpected token '}'",
    "ReferenceError: taskService is not defined",
    # Go
    "./main.go:12:2: undefined: fmt.Printl",
    # generic test failure
    "TEST FAILURES — fix these before continuing:\nFrontend: 0/1 passed",
])
def test_code_failures_are_not_misread_as_infrastructure(output):
    result = classify_failure(output)
    assert not result.is_infrastructure, (
        f"MUST stay a code failure or the fix loop is suppressed: {output[:60]!r}"
    )
    assert result.is_code


# ── degenerate input defaults to "code" so the loop still runs ──────────────

@pytest.mark.parametrize("output", ["", None, "   ", 12345, [], {"a": 1}])
def test_unrecognised_or_invalid_input_defaults_to_code(output):
    assert classify_failure(output).is_infrastructure is False


def test_unknown_message_defaults_to_code():
    assert not is_infrastructure_failure("something nobody has seen before")


# ── the real-world composite case ───────────────────────────────────────────

def test_full_maven_failure_block_from_the_live_java_job():
    """The exact smoke_test_container.log body that produced '42 issues'."""
    output = (
        "[ERROR] Could not create local repository at /home/default/.m2/repository -> [Help 1]\n"
        "[ERROR] \n"
        "[ERROR] To see the full stack trace of the errors, re-run Maven with the -e switch.\n"
        "[ERROR] Re-run Maven using the -X switch to enable full debug logging.\n"
    )
    result = classify_failure(output)
    assert result.is_infrastructure
    assert "local repository" in result.reason.lower()


def test_infra_signature_wins_when_mixed_with_noise():
    """Real logs interleave the platform error with ordinary build chatter."""
    output = (
        "[INFO] Scanning for projects...\n"
        "[INFO] Building task-api 1.0\n"
        "[ERROR] Could not create local repository at /home/default/.m2/repository\n"
    )
    assert is_infrastructure_failure(output)


def test_helper_matches_classify():
    msg = "Could not transfer artifact org.h2:h2:jar:2.2.224"
    assert is_infrastructure_failure(msg) == classify_failure(msg).is_infrastructure


# ── the loop must actually stop, not just classify ──────────────────────────

class _FakeDB:
    def __init__(self):
        self.issues = []

    def create_validation_issue(self, *args, **kwargs):
        self.issues.append((args, kwargs))


def _workflow_with_report(report, tmp_path):
    """A SoftwareDevWorkflow with just enough wired up to drive the fix loop."""
    from llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow as W

    wf = W.__new__(W)
    wf.job_db = _FakeDB()
    wf.project_id = "job-1"
    wf.workspace_path = tmp_path
    wf._validation_report = None
    wf._run_count = 0

    def _suite():
        wf._run_count += 1
        return report

    wf._run_validation_suite = _suite
    wf._max_post_build_iterations = lambda: 6
    return wf


def test_infra_failure_stops_the_loop_after_one_iteration(tmp_path):
    """
    The whole point: one Maven error must not become six rewrite rounds.
    """
    report = {
        "overall": "ISSUES_FOUND",
        "checks": {
            "smoke_test": {
                "pass": False,
                "result": "[ERROR] Could not create local repository at /home/default/.m2/repository",
            }
        },
    }
    wf = _workflow_with_report(report, tmp_path)
    wf._run_post_build_fix_iteration()

    assert wf._run_count == 1, (
        f"infra failure should stop after 1 validation pass, ran {wf._run_count}"
    )
    assert wf.job_db.issues, "the platform failure must be recorded for the UI"
    recorded = str(wf.job_db.issues[0])
    assert "infrastructure" in recorded
    assert "local repository" in recorded.lower()


def test_code_failure_still_drives_the_normal_fix_loop(tmp_path):
    """The classifier must not short-circuit genuine compile errors."""
    report = {
        "overall": "ISSUES_FOUND",
        "checks": {
            "smoke_test": {
                "pass": False,
                "result": "[ERROR] Task.java:[42,15] cannot find symbol",
            }
        },
    }
    wf = _workflow_with_report(report, tmp_path)
    wf._collect_fixable_issues = lambda r: []  # no attributable files -> clean exit

    wf._run_post_build_fix_iteration()

    # The existing code legitimately records the failing smoke_test check; what
    # must NOT appear is an "infrastructure" classification, which would mean
    # the loop wrongly gave up on a real compile error.
    recorded = " ".join(str(i) for i in wf.job_db.issues)
    assert "infrastructure" not in recorded, (
        "a genuine compile error must not be recorded as an infrastructure failure"
    )


def test_detector_ignores_passing_checks(tmp_path):
    report = {
        "overall": "ISSUES_FOUND",
        "checks": {"smoke_test": {"pass": True, "result": "Could not resolve dependencies"}},
    }
    wf = _workflow_with_report(report, tmp_path)
    assert wf._detect_infrastructure_failure(report) is None


def test_detector_covers_the_feature_test_bed_check(tmp_path):
    report = {
        "overall": "ISSUES_FOUND",
        "checks": {
            "feature_test_bed": {
                "pass": False,
                "result": "No container image configured for project type 'static'",
            }
        },
    }
    wf = _workflow_with_report(report, tmp_path)
    assert wf._detect_infrastructure_failure(report) is not None


def test_recording_survives_a_broken_db(tmp_path):
    """Bookkeeping must never be what kills a job."""
    report = {
        "overall": "ISSUES_FOUND",
        "checks": {"smoke_test": {"pass": False, "result": "Network is unreachable"}},
    }
    wf = _workflow_with_report(report, tmp_path)

    class Boom:
        def create_validation_issue(self, *a, **k):
            raise RuntimeError("db gone")

    wf.job_db = Boom()
    wf._run_post_build_fix_iteration()  # must not raise
