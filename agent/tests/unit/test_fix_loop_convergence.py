"""
The fix loop must stop when it is churning rather than converging.

``max_stagnant_rounds`` only fires when the issue *set* is identical between
rounds. Job d32dcaf7 ran 48 → 67 → 52 → 56 and never tripped it: each pass
edited different files, so the set kept changing and churn read as progress.
Four iterations and ~40 minutes were spent rewriting correct code.

The existing docstring names the constraint that makes this subtle, and it is
correct: *a rising count can mean progress* — fixing a parse error lets the
build advance and surface real errors that were previously unreachable. So
"stop when the count stops falling" is wrong and would abandon healthy runs.

The metric that satisfies both: **rounds since the best (lowest) count
improved**. A rise followed by a fall keeps going; a plateau or oscillation
around a level stops.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))


class _FakeDB:
    def __init__(self):
        self.issues = []

    def create_validation_issue(self, *a, **k):
        self.issues.append((a, k))


def _workflow(tmp_path, counts):
    """A workflow whose validation returns len(counts[i]) issues on iteration i."""
    from llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow as W

    wf = W.__new__(W)
    wf.job_db = _FakeDB()
    wf.project_id = "job-1"
    wf.workspace_path = tmp_path
    wf._validation_report = None
    wf._report_progress = lambda *a, **k: None
    wf.iterations_run = 0
    wf._seq = list(counts)

    def _suite():
        wf.iterations_run += 1
        return {"overall": "ISSUES_FOUND", "checks": {}}

    def _collect(_report):
        idx = min(wf.iterations_run - 1, len(wf._seq) - 1)
        n = wf._seq[idx]
        # Distinct signatures each round, mimicking the real churn: different
        # files touched every pass, so the set-identity check never fires.
        return [
            {"file": f"F{wf.iterations_run}_{i}.java", "check": "smoke_test",
             "description": f"issue {i} round {wf.iterations_run}"}
            for i in range(n)
        ]

    wf._run_validation_suite = _suite
    wf._collect_fixable_issues = _collect
    wf._max_post_build_iterations = lambda: 6
    wf._repair_build_manifest = lambda: False
    wf._detect_infrastructure_failure = lambda r: None
    wf._auto_fix_issues = lambda issues: []
    wf._build_wiring_allowlist = lambda: set()
    wf._run_post_build_fix_with_context = lambda *a, **k: None
    wf.task_manager = type("TM", (), {"get_registered_file_paths": lambda self: set()})()
    wf.dev_agent = object()
    wf.agent_backstories = {}
    wf.budget_tracker = None
    wf.config = None
    return wf


def test_oscillation_stops_before_exhausting_the_budget(tmp_path):
    """The live sequence: 48 -> 67 -> 52 -> 56, never improving on 48."""
    wf = _workflow(tmp_path, [48, 67, 52, 56, 50, 54])
    wf._run_post_build_fix_iteration()

    assert wf.iterations_run < 6, (
        f"churn should stop early, ran all {wf.iterations_run} iterations"
    )


def test_genuine_progress_is_not_cut_short(tmp_path):
    """
    A rise then a fall is the healthy case the original docstring protects:
    fixing a parse error surfaces real errors that were previously unreachable.
    """
    wf = _workflow(tmp_path, [10, 25, 8, 3, 1, 0])
    wf._run_post_build_fix_iteration()

    assert wf.iterations_run >= 5, (
        f"steady improvement must not be stopped, only ran {wf.iterations_run}"
    )


def test_steady_decrease_runs_to_completion(tmp_path):
    # The loop makes one final validation call after the last iteration, so a
    # full 6-iteration run performs 7 validations.
    wf = _workflow(tmp_path, [20, 15, 10, 5, 2, 1])
    wf._run_post_build_fix_iteration()
    assert wf.iterations_run >= 6


def test_flat_plateau_stops(tmp_path):
    """Same count every round with different files — the classic stall."""
    wf = _workflow(tmp_path, [30, 30, 30, 30, 30, 30])
    wf._run_post_build_fix_iteration()
    assert wf.iterations_run < 6


def test_stopping_records_why(tmp_path):
    """A silent stop looks identical to success in the UI."""
    wf = _workflow(tmp_path, [48, 67, 52, 56, 50, 54])
    wf._run_post_build_fix_iteration()

    recorded = " ".join(str(i) for i in wf.job_db.issues).lower()
    assert recorded, "the loop must say why it gave up"
    assert "converg" in recorded or "progress" in recorded or "stall" in recorded


def test_single_iteration_is_never_cut_off(tmp_path):
    """One round cannot be judged as non-converging."""
    wf = _workflow(tmp_path, [5])
    wf._run_post_build_fix_iteration()
    assert wf.iterations_run >= 1
