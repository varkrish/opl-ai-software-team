"""The post-build fix loop must keep going while it is making progress.

The loop previously stopped unless the issue *count* strictly decreased. That
is wrong for compiled languages: a compiler aborts at the first failing unit, so
fixing three errors routinely lets the build advance and surface five more. The
count rises, and the old heuristic declared "converged" and quit at exactly the
moment real progress was happening.

Stagnation is now judged by issue *identity* — the same issues coming back
unchanged — so a rising count no longer aborts the loop.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow as W


def _issue(check="smoke_test", file="main.go", desc="undefined: x"):
    return {"check": check, "file": file, "description": desc}


class _Loop:
    """Drives _run_post_build_fix_iteration over a scripted sequence of rounds."""

    def __init__(self, rounds):
        self.rounds = rounds
        self.fix_calls = []
        self.validations = 0

    def run(self, tmp_path):
        wf = W.__new__(W)
        wf.workspace_path = tmp_path
        wf.dev_agent = object()  # non-None: skip DevAgent construction
        wf._validation_report = {}
        wf.job_db = None  # no persistence in this harness

        def validation_suite():
            idx = min(self.validations, len(self.rounds) - 1)
            self.validations += 1
            issues = self.rounds[idx]
            return {"overall": "PASS" if issues is None else "ISSUES_FOUND",
                    "checks": {}, "_issues": issues}

        wf._run_validation_suite = validation_suite
        wf._collect_fixable_issues = lambda report: list(report.get("_issues") or [])
        wf._auto_fix_issues = lambda fixable: []
        wf._report_progress = lambda *a, **kw: None
        wf._build_wiring_allowlist = lambda: None
        wf._run_post_build_fix_with_context = (
            lambda fp, descs, all_files: self.fix_calls.append(fp)
        )
        wf.task_manager = type("TM", (), {"get_registered_file_paths": lambda self: set()})()

        wf._run_post_build_fix_iteration()
        return wf


def test_rising_issue_count_does_not_stop_the_loop(tmp_path, monkeypatch):
    """The regression this module exists for: 1 issue -> 3 issues is progress."""
    monkeypatch.setenv("MAX_POST_BUILD_ITERATIONS", "4")
    loop = _Loop([
        [_issue(desc="err A")],
        [_issue(desc="err B"), _issue(desc="err C"), _issue(desc="err D")],
        None,  # PASS
    ])
    loop.run(tmp_path)
    # Round 2 must have run despite the count going 1 -> 3, then round 3 saw PASS.
    # Issues group by file, so each round is one fix call for main.go.
    assert loop.validations == 3
    assert loop.fix_calls == ["main.go", "main.go"]


def test_identical_issues_stop_the_loop(tmp_path, monkeypatch):
    """Nothing changing across rounds means the agent is stuck — stop burning tokens."""
    monkeypatch.setenv("MAX_POST_BUILD_ITERATIONS", "10")
    same = [_issue(desc="always the same")]
    loop = _Loop([same, same, same, same, same])
    loop.run(tmp_path)
    # Round 1 fixes; rounds 2 and 3 are identical -> stop on the 2nd repeat.
    assert loop.validations == 3


def test_stops_immediately_on_pass(tmp_path, monkeypatch):
    monkeypatch.setenv("MAX_POST_BUILD_ITERATIONS", "5")
    loop = _Loop([None])
    loop.run(tmp_path)
    assert loop.validations == 1
    assert loop.fix_calls == []


def test_respects_iteration_ceiling(tmp_path, monkeypatch):
    """Always-changing issues must still be bounded: 3 rounds + 1 final re-validate."""
    monkeypatch.setenv("MAX_POST_BUILD_ITERATIONS", "3")
    loop = _Loop([[_issue(desc=f"error {i}")] for i in range(20)])
    loop.run(tmp_path)
    assert loop.validations == 4


def test_all_attributed_files_get_a_fix_call(tmp_path, monkeypatch):
    monkeypatch.setenv("MAX_POST_BUILD_ITERATIONS", "1")
    loop = _Loop([[
        _issue(file="main.go"), _issue(file="server.js"), _issue(file="app.py"),
    ]])
    loop.run(tmp_path)
    assert set(loop.fix_calls) == {"main.go", "server.js", "app.py"}


# ── iteration budget ─────────────────────────────────────────────────────────

def test_budget_is_higher_when_code_is_executed(monkeypatch):
    """Cascading compile errors need more passes than static checks."""
    monkeypatch.delenv("MAX_POST_BUILD_ITERATIONS", raising=False)
    monkeypatch.setenv("SMOKE_TEST_BACKEND", "sandbox_api")
    assert W._max_post_build_iterations(W.__new__(W)) == 6


def test_budget_default_for_static_only(monkeypatch):
    monkeypatch.delenv("MAX_POST_BUILD_ITERATIONS", raising=False)
    monkeypatch.setenv("SMOKE_TEST_BACKEND", "syntax_only")
    assert W._max_post_build_iterations(W.__new__(W)) == 3


def test_explicit_override_wins(monkeypatch):
    monkeypatch.setenv("MAX_POST_BUILD_ITERATIONS", "9")
    monkeypatch.setenv("SMOKE_TEST_BACKEND", "sandbox_api")
    assert W._max_post_build_iterations(W.__new__(W)) == 9


# ── signatures ───────────────────────────────────────────────────────────────

def test_signatures_distinguish_different_errors():
    a = W._issue_signatures([_issue(desc="undefined: x")])
    b = W._issue_signatures([_issue(desc="undefined: y")])
    assert a != b


def test_signatures_are_order_independent():
    one, two = _issue(file="a.go"), _issue(file="b.go")
    assert W._issue_signatures([one, two]) == W._issue_signatures([two, one])
