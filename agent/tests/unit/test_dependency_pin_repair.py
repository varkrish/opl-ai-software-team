"""
A test run that never started is not a failing test suite.

``_run_feature_test_bed_loop`` reads the runner's exit code, and any non-zero
value becomes "TEST FAILURES — fix these before continuing", which sends
DevAgent to patch test code for ``MAX_TEST_ITERATIONS`` rounds. It never asks
whether a test actually executed.

Live, job 107b3d3e. test_plan.md said::

    backend_test_command: pip install -r backend/requirements.txt && pytest tests

and requirements.txt pinned a version that does not exist::

    ERROR: Could not find a version that satisfies the requirement
    memmachine-client==0.1.5 (from versions: 0.2.4, 0.2.5, 0.2.6, 0.3.0, 0.3.1,
    0.3.2, 0.3.3, 0.3.4, 0.3.5, 0.3.6)

``&&`` short-circuited, pytest never ran, and the job spent three DevAgent
iterations editing tests to fix a one-line version pin — then recorded
"Tests still failing after 3 iterations". Same misattribution as the Maven
parent-POM defect: the failure is not in the file it is attributed to, so the
loop cannot converge.

Both halves are deterministic. An unresolvable pin is unambiguous, and pip
prints the resolvable versions in the same message — the repair is a lookup in
the error text, not a judgement. npm reports the same class (``ETARGET``) but
does *not* list available versions, so it is detected and reported and never
guessed at, matching manifest_repair's existing "escalate rather than invent"
rule for undefined POM properties.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from llamaindex_crew.utils.manifest_repair import (  # noqa: E402
    DependencyDefect,
    detect_dependency_resolution_failure,
    repair_unresolvable_pins,
)

PIP_ERROR = (
    "Collecting fastapi==0.110.0\n"
    "  Downloading fastapi-0.110.0-py3-none-any.whl (92 kB)\n"
    "ERROR: Could not find a version that satisfies the requirement "
    "memmachine-client==0.1.5 (from versions: 0.2.4, 0.2.5, 0.2.6, 0.3.0, "
    "0.3.1, 0.3.2, 0.3.3, 0.3.4, 0.3.5, 0.3.6)\n"
    "ERROR: No matching distribution found for memmachine-client==0.1.5\n"
)

REQUIREMENTS = (
    "fastapi==0.110.0\n"
    "uvicorn[standard]==0.27.0\n"
    "memmachine-client==0.1.5\n"
    "neo4j==5.14.0\n"
    "python-dotenv==1.0.0\n"
    "sse-starlette==1.6.5\n"
)


# ── detection ───────────────────────────────────────────────────────────────

def test_detects_the_live_pip_failure():
    defect = detect_dependency_resolution_failure(PIP_ERROR)

    assert isinstance(defect, DependencyDefect)
    assert defect.package == "memmachine-client"
    assert defect.requested == "0.1.5"
    assert defect.repairable is True
    assert "0.3.6" in defect.available


def test_a_real_test_failure_is_not_a_dependency_defect():
    """The common case must stay on the normal fix path."""
    output = (
        "collected 12 items\n"
        "tests/test_services.py::test_summary FAILED\n"
        "E   AssertionError: assert 300 == 250\n"
        "=========== 1 failed, 11 passed in 0.42s ===========\n"
    )
    assert detect_dependency_resolution_failure(output) is None


def test_empty_or_unrecognised_output_is_not_a_dependency_defect():
    for output in ("", None, "some unrelated stack trace", "Killed"):
        assert detect_dependency_resolution_failure(output) is None


def test_npm_target_failure_is_detected_but_not_repairable():
    """npm names the package but never lists the versions, so nothing to pick."""
    output = (
        "npm ERR! code ETARGET\n"
        "npm ERR! notarget No matching version found for recharts@^99.0.0.\n"
    )
    defect = detect_dependency_resolution_failure(output)

    assert defect is not None
    assert defect.package == "recharts"
    assert defect.repairable is False, "must not invent a version npm did not offer"


def test_a_network_outage_is_not_reported_as_a_bad_pin():
    """No egress is infrastructure — failure_classifier's job, not this one."""
    output = (
        "WARNING: Retrying after connection broken by NewConnectionError\n"
        "ERROR: Could not find a version that satisfies the requirement fastapi\n"
        "ERROR: No matching distribution found for fastapi\n"
    )
    defect = detect_dependency_resolution_failure(output)

    assert defect is None or defect.repairable is False, (
        "an empty candidate list means the index was unreachable, not a bad pin"
    )


# ── repair ──────────────────────────────────────────────────────────────────

def test_repairs_the_pin_to_the_newest_stable_version(tmp_path):
    (tmp_path / "backend").mkdir()
    req = tmp_path / "backend" / "requirements.txt"
    req.write_text(REQUIREMENTS, encoding="utf-8")

    repairs = repair_unresolvable_pins(tmp_path, PIP_ERROR)

    assert repairs, "the live defect must be repaired"
    text = req.read_text(encoding="utf-8")
    assert "memmachine-client==0.3.6" in text
    assert "0.1.5" not in text


def test_other_pins_are_left_exactly_as_they_were(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text(REQUIREMENTS, encoding="utf-8")

    repair_unresolvable_pins(tmp_path, PIP_ERROR)
    text = req.read_text(encoding="utf-8")

    for untouched in ("fastapi==0.110.0", "uvicorn[standard]==0.27.0",
                      "neo4j==5.14.0", "python-dotenv==1.0.0",
                      "sse-starlette==1.6.5"):
        assert untouched in text


def test_prereleases_are_not_chosen(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text("widget==1.0.0\n", encoding="utf-8")
    output = (
        "ERROR: Could not find a version that satisfies the requirement "
        "widget==1.0.0 (from versions: 2.0.0, 2.1.0, 3.0.0rc1, 3.0.0b2)\n"
    )

    repair_unresolvable_pins(tmp_path, output)

    assert "widget==2.1.0" in req.read_text(encoding="utf-8")


def test_versions_are_compared_numerically_not_lexically(tmp_path):
    """'0.10.0' beats '0.9.0'; string ordering gets this backwards."""
    req = tmp_path / "requirements.txt"
    req.write_text("widget==0.1.0\n", encoding="utf-8")
    output = (
        "ERROR: Could not find a version that satisfies the requirement "
        "widget==0.1.0 (from versions: 0.9.0, 0.10.0, 0.2.0)\n"
    )

    repair_unresolvable_pins(tmp_path, output)

    assert "widget==0.10.0" in req.read_text(encoding="utf-8")


def test_repair_is_idempotent(tmp_path):
    """The loop retries after a repair; a second pass must not spin."""
    req = tmp_path / "requirements.txt"
    req.write_text(REQUIREMENTS, encoding="utf-8")

    assert repair_unresolvable_pins(tmp_path, PIP_ERROR)
    assert repair_unresolvable_pins(tmp_path, PIP_ERROR) == [], (
        "the pin is already correct; reporting a repair again would loop"
    )


def test_nothing_is_written_when_the_package_is_absent(tmp_path):
    req = tmp_path / "requirements.txt"
    original = "fastapi==0.110.0\n"
    req.write_text(original, encoding="utf-8")

    assert repair_unresolvable_pins(tmp_path, PIP_ERROR) == []
    assert req.read_text(encoding="utf-8") == original


def test_missing_requirements_file_does_not_raise(tmp_path):
    assert repair_unresolvable_pins(tmp_path, PIP_ERROR) == []


def test_unrepairable_npm_defect_writes_nothing(tmp_path):
    pkg = tmp_path / "package.json"
    original = json.dumps({"dependencies": {"recharts": "^99.0.0"}})
    pkg.write_text(original, encoding="utf-8")
    output = "npm ERR! notarget No matching version found for recharts@^99.0.0.\n"

    assert repair_unresolvable_pins(tmp_path, output) == []
    assert pkg.read_text(encoding="utf-8") == original


def test_requirements_are_found_in_nested_service_directories(tmp_path):
    """The live job's manifest was at backend/requirements.txt, not the root."""
    (tmp_path / "services" / "api").mkdir(parents=True)
    req = tmp_path / "services" / "api" / "requirements.txt"
    req.write_text(REQUIREMENTS, encoding="utf-8")

    assert repair_unresolvable_pins(tmp_path, PIP_ERROR)
    assert "memmachine-client==0.3.6" in req.read_text(encoding="utf-8")


def test_repair_message_names_the_package_and_both_versions(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text(REQUIREMENTS, encoding="utf-8")

    repairs = repair_unresolvable_pins(tmp_path, PIP_ERROR)

    assert len(repairs) == 1
    assert "memmachine-client" in repairs[0]
    assert "0.1.5" in repairs[0] and "0.3.6" in repairs[0]


# ── the loop must not send DevAgent after tests that never ran ──────────────

class _RecordingDevAgent:
    def __init__(self):
        self.prompts = []
        self.agent = self

    def reset_chat(self):
        pass

    def chat(self, prompt):
        self.prompts.append(prompt)
        return "ok"


class _FakeDB:
    def __init__(self):
        self.issues = []

    def create_validation_issue(self, **kwargs):
        self.issues.append(kwargs)


def _workflow(tmp_path, monkeypatch, results):
    """A workflow whose test runner returns results[i] on call i (last repeats)."""
    from llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow as W
    from llamaindex_crew.tools import test_tools

    monkeypatch.setenv("SMOKE_TEST_BACKEND", "container")
    monkeypatch.setenv("MAX_TEST_ITERATIONS", "3")

    wf = W.__new__(W)
    wf.workspace_path = tmp_path
    wf.project_id = "job-1"
    wf.job_db = _FakeDB()
    wf.dev_agent = _RecordingDevAgent()
    wf.task_manager = type(
        "TM", (), {"update_task_status_by_output": lambda self, out: None}
    )()
    wf._load_job_metadata = lambda: {}
    wf._update_job_metadata = lambda meta: None
    wf.calls = []

    def _run(layer, ws):
        idx = min(len(wf.calls) // 2, len(results) - 1)
        wf.calls.append(layer)
        return dict(results[idx])

    monkeypatch.setattr(test_tools, "run_feature_tests", _run)
    return wf


def test_a_failed_install_repairs_the_pin_instead_of_editing_tests(tmp_path, monkeypatch):
    """Job 107b3d3e burned three DevAgent rounds on a one-line version pin."""
    req = tmp_path / "requirements.txt"
    req.write_text(REQUIREMENTS, encoding="utf-8")

    wf = _workflow(tmp_path, monkeypatch, [
        {"passed": False, "raw_output": PIP_ERROR,
         "failures": [{"test": "runner", "error": PIP_ERROR}]},
        {"passed": True},
    ])
    wf._run_feature_test_bed_loop()

    assert "memmachine-client==0.3.6" in req.read_text(encoding="utf-8")
    assert wf.dev_agent.prompts == [], (
        "no test ran, so there is nothing for DevAgent to fix in the tests"
    )


def test_an_unrepairable_dependency_failure_is_reported_accurately(tmp_path, monkeypatch):
    npm_err = "npm ERR! notarget No matching version found for recharts@^99.0.0.\n"
    wf = _workflow(tmp_path, monkeypatch, [
        {"passed": False, "raw_output": npm_err,
         "failures": [{"test": "runner", "error": npm_err}]},
    ])
    wf._run_feature_test_bed_loop()

    recorded = " ".join(str(i) for i in wf.job_db.issues)
    assert wf.job_db.issues, "a blocked test run must be visible"
    assert "recharts" in recorded, f"issue must name the dependency: {recorded}"
    assert wf.dev_agent.prompts == []


def test_a_genuine_test_failure_still_reaches_dev_agent(tmp_path, monkeypatch):
    """The normal path must be untouched."""
    failure = (
        "collected 3 items\n"
        "tests/test_services.py::test_total FAILED\n"
        "E   AssertionError: assert 300 == 250\n"
        "=========== 1 failed, 2 passed in 0.3s ===========\n"
    )
    wf = _workflow(tmp_path, monkeypatch, [
        {"passed": False, "raw_output": failure, "total": 3, "passed_count": 2,
         "failures": [{"test": "test_total", "error": "assert 300 == 250"}]},
    ])
    wf._run_feature_test_bed_loop()

    assert wf.dev_agent.prompts, "real test failures must still be fixed"
    assert "TEST FAILURES" in wf.dev_agent.prompts[0]
