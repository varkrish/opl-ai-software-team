"""Failing validation checks must be persisted so the UI can explain them.

``validation_issues`` was populated only from the external validator service, so
checks owned by the in-process suite — smoke_test above all — produced a job
marked ``completed_with_errors`` with an empty issues table. The UI panel reads
that table, found nothing, and rendered null: the user saw a failed build with
no stated reason.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow as W


class _FakeDB:
    def __init__(self):
        self.issues = []

    def create_validation_issue(self, **kwargs):
        self.issues.append(kwargs)
        return kwargs


@pytest.fixture
def wf(tmp_path):
    instance = W.__new__(W)
    instance.workspace_path = tmp_path
    instance.project_id = "job-1"
    instance.job_db = _FakeDB()
    (tmp_path / "main.go").write_text("package main\n", encoding="utf-8")
    return instance


def _report(overall="ISSUES_FOUND", **checks):
    return {"overall": overall, "checks": checks}


def test_persists_failing_smoke_test_with_output(wf):
    wf._validation_report = _report(
        smoke_test={"pass": False, "result": "❌ build failed\n./main.go:5:2: undefined: x"}
    )
    wf._persist_unresolved_validation_issues()

    assert len(wf.job_db.issues) == 1
    issue = wf.job_db.issues[0]
    assert issue["check_name"] == "smoke_test"
    assert "undefined: x" in issue["description"]
    # A single attributable file gets pinned to the issue.
    assert issue["file_path"] == "main.go"


def test_no_file_attribution_leaves_file_path_null(wf):
    wf._validation_report = _report(
        smoke_test={"pass": False, "result": "container exited with code 137"}
    )
    wf._persist_unresolved_validation_issues()
    assert wf.job_db.issues[0]["file_path"] is None


def test_passing_checks_are_not_persisted(wf):
    wf._validation_report = _report(
        smoke_test={"pass": True, "result": "✅ passed"},
        module_system={"pass": True},
    )
    wf._persist_unresolved_validation_issues()
    assert wf.job_db.issues == []


def test_overall_pass_persists_nothing(wf):
    wf._validation_report = _report(overall="PASS", smoke_test={"pass": False})
    wf._persist_unresolved_validation_issues()
    assert wf.job_db.issues == []


def test_check_without_detail_still_gets_a_description(wf):
    """An empty description would render as a blank row in the UI."""
    wf._validation_report = _report(entrypoint={"pass": False})
    wf._persist_unresolved_validation_issues()
    assert "entrypoint" in wf.job_db.issues[0]["description"]


def test_every_failing_check_is_recorded(wf):
    wf._validation_report = _report(
        smoke_test={"pass": False, "result": "boom"},
        module_system={"pass": False},
        entrypoint={"pass": True},
    )
    wf._persist_unresolved_validation_issues()
    assert {i["check_name"] for i in wf.job_db.issues} == {"smoke_test", "module_system"}


def test_missing_job_db_is_tolerated(tmp_path):
    """Called on partially-built workflows in tests and resume paths."""
    instance = W.__new__(W)
    instance.workspace_path = tmp_path
    instance._validation_report = _report(smoke_test={"pass": False})
    instance._persist_unresolved_validation_issues()  # must not raise


def test_db_failure_does_not_break_the_build(wf):
    """Persistence is reporting, not correctness — it must never fail a job."""
    def boom(**kwargs):
        raise RuntimeError("db down")

    wf.job_db.create_validation_issue = boom
    wf._validation_report = _report(smoke_test={"pass": False, "result": "boom"})
    wf._persist_unresolved_validation_issues()  # must not raise


def test_long_output_is_truncated(wf):
    wf._validation_report = _report(
        smoke_test={"pass": False, "result": "x" * 10000}
    )
    wf._persist_unresolved_validation_issues()
    assert len(wf.job_db.issues[0]["description"]) <= 4000
