"""Live preview: start a job's generated app in a sandbox and expose its URL.

Detection is the risky part — generated projects vary, and a wrong start command
produces a sandbox that burns a published port while serving nothing. These
tests pin the detection rules and, more importantly, that every failure path
tears the sandbox down instead of leaking it.
"""
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(_ROOT / "crew_studio"))
sys.path.insert(0, str(_ROOT / "agent" / "src"))

import preview_runner as pr


# ── start-command detection ──────────────────────────────────────────────────

def test_explicit_preview_command_wins(tmp_path):
    """A project can always state its own command; detection must not override."""
    (tmp_path / "test_plan.md").write_text(
        "backend_test_command: pytest\npreview_command: uvicorn app:app --port 9001\n",
        encoding="utf-8",
    )
    (tmp_path / "main.py").write_text("", encoding="utf-8")
    command, port = pr.detect_preview(tmp_path, "python")
    assert command == "uvicorn app:app --port 9001"
    assert port == 9001


@pytest.mark.parametrize("entry", ["main.py", "app.py", "server.py", "run.py"])
def test_python_entrypoints(tmp_path, entry):
    (tmp_path / entry).write_text("", encoding="utf-8")
    command, port = pr.detect_preview(tmp_path, "python")
    assert command.endswith(f"python3 {entry}")
    assert port == pr.DEFAULT_PORT


def test_python_installs_requirements_when_present(tmp_path):
    (tmp_path / "main.py").write_text("", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("flask\n", encoding="utf-8")
    command, _ = pr.detect_preview(tmp_path, "python")
    assert "pip install" in command and command.strip().endswith("python3 main.py")


def test_python_without_entrypoint_is_an_error(tmp_path):
    (tmp_path / "helper.py").write_text("", encoding="utf-8")
    with pytest.raises(pr.PreviewError, match="entrypoint"):
        pr.detect_preview(tmp_path, "python")


def test_domain_named_file_with_main_guard_is_the_entrypoint(tmp_path):
    """Generated apps often name the entrypoint after the domain, e.g. todo.py."""
    (tmp_path / "todo.py").write_text(
        "def main():\n    pass\n\nif __name__ == '__main__':\n    main()\n",
        encoding="utf-8",
    )
    (tmp_path / "helpers.py").write_text("X = 1\n", encoding="utf-8")
    command, _ = pr.detect_preview(tmp_path, "python")
    assert command.endswith("python3 todo.py")


def test_conventional_name_beats_main_guard(tmp_path):
    (tmp_path / "app.py").write_text("", encoding="utf-8")
    (tmp_path / "todo.py").write_text(
        "if __name__ == '__main__':\n    pass\n", encoding="utf-8"
    )
    command, _ = pr.detect_preview(tmp_path, "python")
    assert command.endswith("python3 app.py")


def test_multiple_main_guards_are_ambiguous(tmp_path):
    """Guessing between two runnable files would start the wrong app."""
    for name in ("todo.py", "report.py"):
        (tmp_path / name).write_text(
            "if __name__ == '__main__':\n    pass\n", encoding="utf-8"
        )
    with pytest.raises(pr.PreviewError, match="entrypoint"):
        pr.detect_preview(tmp_path, "python")


def test_node_prefers_start_over_dev(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"dev": "vite", "start": "node server.js"}}),
        encoding="utf-8",
    )
    command, _ = pr.detect_preview(tmp_path, "node")
    assert command.endswith("npm start")


def test_node_falls_back_to_dev(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"dev": "vite"}}), encoding="utf-8"
    )
    command, _ = pr.detect_preview(tmp_path, "node")
    assert command.endswith("npm run dev")


def test_node_without_runnable_script_is_an_error(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "jest"}}), encoding="utf-8"
    )
    with pytest.raises(pr.PreviewError):
        pr.detect_preview(tmp_path, "node")


def test_unsupported_types_are_explicit(tmp_path):
    for project_type in ("java_maven", "java_gradle", "unknown"):
        with pytest.raises(pr.PreviewError):
            pr.detect_preview(tmp_path, project_type)


@pytest.mark.parametrize("command,expected", [
    ("uvicorn app:app --port 9001", 9001),
    ("node server.js --port=3000", 3000),
    ("PORT=5000 npm start", 5000),
    ("python3 -m http.server", pr.DEFAULT_PORT),
])
def test_port_extraction(tmp_path, command, expected):
    (tmp_path / "test_plan.md").write_text(f"preview_command: {command}\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("", encoding="utf-8")
    assert pr.detect_preview(tmp_path, "python")[1] == expected


# ── stored state ─────────────────────────────────────────────────────────────

def test_state_round_trip():
    job = {"metadata": json.dumps({"live_preview": {"sandbox_id": "sb-1"}})}
    assert pr.get_preview_state(job)["sandbox_id"] == "sb-1"


def test_state_read_from_decoded_dict():
    """JobDatabase.get_job returns metadata already decoded — the common path."""
    job = {"metadata": {"live_preview": {"sandbox_id": "sb-1"}}}
    assert pr.get_preview_state(job)["sandbox_id"] == "sb-1"


@pytest.mark.parametrize("metadata", [
    None, "", "{}", {}, "not json", '{"live_preview": "nope"}', {"live_preview": "nope"},
])
def test_missing_or_corrupt_state_is_empty(metadata):
    assert pr.get_preview_state({"metadata": metadata}) == {}


@pytest.mark.parametrize("existing", [
    {"skills_used": ["python"], "loop_state": {"iteration": 2}},
    json.dumps({"skills_used": ["python"], "loop_state": {"iteration": 2}}),
])
def test_starting_a_preview_preserves_other_metadata(python_job, existing):
    """Treating decoded metadata as unparseable silently wiped every other key."""
    job, db = python_job
    db.job = {**job, "metadata": existing}

    pr.start_preview(db, db.job)

    metadata = json.loads(db.updates[-1]["metadata"])
    assert metadata["skills_used"] == ["python"]
    assert metadata["loop_state"] == {"iteration": 2}
    assert metadata["live_preview"]["sandbox_id"] == "sb-1"


def test_stopping_a_preview_preserves_other_metadata(python_job):
    job, db = python_job
    db.job = {**job, "metadata": {"skills_used": ["go"]}}

    pr.start_preview(db, db.job)
    pr.stop_preview(db, db.job)

    metadata = json.loads(db.updates[-1]["metadata"])
    assert metadata["skills_used"] == ["go"]
    assert "live_preview" not in metadata


# ── lifecycle ────────────────────────────────────────────────────────────────

class _FakeDB:
    def __init__(self, job):
        self.job = job
        self.updates = []

    def get_job(self, job_id):
        return self.job

    def update_job(self, job_id, updates):
        self.updates.append(updates)
        self.job = {**self.job, **updates}
        return True


class _FakeClient:
    """Stands in for SandboxClient; records the calls the runner makes."""

    instances = []

    def __init__(self, base_url, timeout=300.0):
        self.calls = []
        self.deleted = []
        self.fail_on = None
        self.preview_url = "http://127.0.0.1:40001"
        self.app_log = "Traceback: app crashed"
        _FakeClient.instances.append(self)

    def execute(self, sandbox_id, command):
        self.calls.append("execute")
        return 0, self.app_log

    def _maybe_fail(self, step):
        if self.fail_on == step:
            from llamaindex_crew.utils.sandbox_client import SandboxError
            raise SandboxError(f"{step} exploded")

    def create_preview(self, image="", timeout_seconds=0, expose_port=0):
        self.calls.append("create")
        self._maybe_fail("create")
        return "sb-1", self.preview_url

    def upload_workspace(self, sandbox_id, workspace):
        self.calls.append("upload")
        self._maybe_fail("upload")

    def start_background(self, sandbox_id, command):
        self.calls.append("start")
        self.started_command = " ".join(command)
        self._maybe_fail("start")

    def delete(self, sandbox_id):
        self.deleted.append(sandbox_id)


@pytest.fixture
def python_job(tmp_path, monkeypatch):
    (tmp_path / "main.py").write_text("print('hi')\n", encoding="utf-8")
    monkeypatch.setenv("SANDBOX_API_URL", "http://sandbox:18080")
    _FakeClient.instances.clear()
    import llamaindex_crew.utils.sandbox_client as sc
    monkeypatch.setattr(sc, "SandboxClient", _FakeClient)
    # Default: the app comes up. Readiness is overridden per-test below.
    monkeypatch.setattr(pr, "_wait_until_serving", lambda url, timeout=None: True)
    job = {"id": "job-1", "workspace_path": str(tmp_path), "metadata": "{}"}
    return job, _FakeDB(job)


def test_start_runs_full_sequence_and_stores_url(python_job):
    job, db = python_job
    state = pr.start_preview(db, job)

    client = _FakeClient.instances[-1]
    assert client.calls == ["create", "upload", "start"]
    assert state["preview_url"] == "http://127.0.0.1:40001"
    assert json.loads(db.updates[-1]["metadata"])["live_preview"]["sandbox_id"] == "sb-1"


@pytest.mark.parametrize("failing_step", ["upload", "start"])
def test_failure_after_create_deletes_the_sandbox(python_job, failing_step):
    """A leaked preview sandbox holds a published port until the janitor reaps it."""
    job, db = python_job

    original_init = _FakeClient.__init__

    def init_with_failure(self, *a, **kw):
        original_init(self, *a, **kw)
        self.fail_on = failing_step

    _FakeClient.__init__ = init_with_failure
    try:
        with pytest.raises(pr.PreviewError):
            pr.start_preview(db, job)
    finally:
        _FakeClient.__init__ = original_init

    assert _FakeClient.instances[-1].deleted == ["sb-1"]


def test_missing_preview_url_is_treated_as_failure(python_job):
    """allow_preview disabled server-side returns no URL — do not report success."""
    job, db = python_job

    original_init = _FakeClient.__init__

    def init_without_url(self, *a, **kw):
        original_init(self, *a, **kw)
        self.preview_url = ""

    _FakeClient.__init__ = init_without_url
    try:
        with pytest.raises(pr.PreviewError, match="allow_preview"):
            pr.start_preview(db, job)
    finally:
        _FakeClient.__init__ = original_init

    assert _FakeClient.instances[-1].deleted == ["sb-1"]


def test_start_requires_configured_url(python_job, monkeypatch):
    job, db = python_job
    monkeypatch.delenv("SANDBOX_API_URL", raising=False)
    with pytest.raises(pr.PreviewError, match="SANDBOX_API_URL"):
        pr.start_preview(db, job)


def test_start_rejects_missing_workspace(python_job):
    job, db = python_job
    job = {**job, "workspace_path": "/nonexistent/path/xyz"}
    with pytest.raises(pr.PreviewError, match="workspace"):
        pr.start_preview(db, job)


# ── readiness ────────────────────────────────────────────────────────────────

def test_app_that_never_serves_is_reported_not_linked(python_job, monkeypatch):
    """A CLI program publishes a port but serves nothing — the browser would
    show an unexplained empty response, so fail with the app's own output."""
    job, db = python_job
    monkeypatch.setattr(pr, "_wait_until_serving", lambda url, timeout=None: False)

    with pytest.raises(pr.PreviewError) as exc:
        pr.start_preview(db, job)

    message = str(exc.value)
    assert "did not start serving" in message
    assert "Traceback: app crashed" in message   # the app log is included
    assert _FakeClient.instances[-1].deleted == ["sb-1"]
    assert pr.get_preview_state(db.job) == {}


def test_readiness_failure_without_log_still_explains(python_job, monkeypatch):
    job, db = python_job
    monkeypatch.setattr(pr, "_wait_until_serving", lambda url, timeout=None: False)

    original_init = _FakeClient.__init__

    def init_no_log(self, *a, **kw):
        original_init(self, *a, **kw)
        self.app_log = ""

    _FakeClient.__init__ = init_no_log
    try:
        with pytest.raises(pr.PreviewError, match="did not start serving"):
            pr.start_preview(db, job)
    finally:
        _FakeClient.__init__ = original_init


def test_app_output_is_redirected_to_a_log(python_job):
    """Without capturing output there is nothing to explain a failure with."""
    job, db = python_job
    pr.start_preview(db, job)
    assert pr.APP_LOG in _FakeClient.instances[-1].started_command


@pytest.mark.parametrize("api_base,preview,expected", [
    # From a container, host loopback is unreachable — reuse the API's host.
    ("http://host.docker.internal:18080", "http://127.0.0.1:40001",
     "http://host.docker.internal:40001"),
    # Running on the host already: leave it alone.
    ("http://localhost:18080", "http://127.0.0.1:40001", "http://127.0.0.1:40001"),
    ("http://127.0.0.1:18080", "http://127.0.0.1:40001", "http://127.0.0.1:40001"),
])
def test_probe_url_targets_a_reachable_host(api_base, preview, expected):
    assert pr.probe_url(preview, api_base) == expected


def test_stop_deletes_and_clears_state(python_job):
    job, db = python_job
    pr.start_preview(db, job)

    assert pr.stop_preview(db, db.job) is True
    assert _FakeClient.instances[-1].deleted == ["sb-1"]
    assert "live_preview" not in json.loads(db.job["metadata"])


def test_stop_without_running_preview_is_a_noop(python_job):
    job, db = python_job
    assert pr.stop_preview(db, job) is False


def test_stop_clears_state_even_if_delete_fails(python_job):
    """A dead sandbox must not strand the UI in a permanently 'running' state."""
    job, db = python_job
    pr.start_preview(db, job)

    def boom(self, sandbox_id):
        raise RuntimeError("sandbox already gone")

    original_delete = _FakeClient.delete
    _FakeClient.delete = boom
    try:
        assert pr.stop_preview(db, db.job) is True
    finally:
        _FakeClient.delete = original_delete

    assert "live_preview" not in json.loads(db.job["metadata"])
