"""Sandbox API client, smoke-test backend, and agent tool.

The Sandbox API runs generated code in an isolated container reached over HTTP,
so the crew can validate code at runtime without a local container runtime or a
shared filesystem. These tests pin the request sequence and the failure modes
that would otherwise only surface against a live service.
"""
import io
import json
import sys
import tarfile
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from llamaindex_crew.tools import test_tools
from llamaindex_crew.tools.sandbox_tools import create_sandbox_tools
from llamaindex_crew.utils import sandbox_client as sc


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("", encoding="utf-8")
    junk = tmp_path / "__pycache__"
    junk.mkdir()
    (junk / "main.cpython-311.pyc").write_bytes(b"\x00\x01")
    return tmp_path


def _sse(*events: tuple) -> bytes:
    """Build an SSE byte stream from (event, data) pairs."""
    return "".join(f"event: {e}\ndata: {d}\n\n" for e, d in events).encode()


class _Recorder:
    """Captures requests and replays canned responses through httpx.MockTransport."""

    def __init__(self, exit_code=0, stdout=("ok",), create_status=200, preview_url=None):
        self.requests = []
        self.exit_code = exit_code
        self.stdout = stdout
        self.create_status = create_status
        self.preview_url = preview_url

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/sandbox/create"):
            if self.create_status != 200:
                return httpx.Response(self.create_status, text="boom")
            body = {"sandbox_id": "sb-1"}
            if self.preview_url:
                body["preview_url"] = self.preview_url
            return httpx.Response(200, json=body)
        if path.endswith("/files"):
            return httpx.Response(204)
        if path.endswith("/execute"):
            events = [("stdout", line) for line in self.stdout]
            events.append(("exit", json.dumps({"code": self.exit_code})))
            return httpx.Response(200, content=_sse(*events))
        return httpx.Response(204)


@pytest.fixture
def mock_api(monkeypatch):
    """Route all httpx calls in sandbox_client through a recorder."""
    def _install(recorder):
        transport = httpx.MockTransport(recorder.handler)

        def post(url, **kw):
            with httpx.Client(transport=transport) as c:
                return c.post(url, **kw)

        def delete(url, **kw):
            with httpx.Client(transport=transport) as c:
                return c.delete(url, **kw)

        class _Stream:
            def __init__(self, method, url, **kw):
                self._c = httpx.Client(transport=transport)
                self._method, self._url, self._kw = method, url, kw

            def __enter__(self):
                self._r = self._c.request(self._method, self._url, **self._kw)
                return self._r

            def __exit__(self, *exc):
                self._c.close()

        monkeypatch.setattr(sc.httpx, "post", post)
        monkeypatch.setattr(sc.httpx, "delete", delete)
        monkeypatch.setattr(sc.httpx, "stream", _Stream)
        return recorder

    return _install


# ── client ───────────────────────────────────────────────────────────────────

def test_full_lifecycle_sequence(mock_api, workspace):
    rec = mock_api(_Recorder(stdout=("line one", "line two")))
    client = sc.SandboxClient("http://sandbox:18080")

    with client.sandbox(image="alpine") as sandbox_id:
        client.upload_workspace(sandbox_id, workspace)
        code, out = client.execute(sandbox_id, ["echo", "hi"])

    assert (code, out) == (0, "line one\nline two")
    # create → upload → execute → delete, in that order
    assert [(r.method, r.url.path) for r in rec.requests] == [
        ("POST", "/sandbox/create"),
        ("POST", "/sandbox/sb-1/files"),
        ("POST", "/sandbox/sb-1/execute"),
        ("DELETE", "/sandbox/sb-1"),
    ]


def test_sandbox_deleted_even_when_body_raises(mock_api, workspace):
    rec = mock_api(_Recorder())
    client = sc.SandboxClient("http://sandbox:18080")

    with pytest.raises(ValueError):
        with client.sandbox() as sandbox_id:
            raise ValueError("agent blew up mid-run")

    assert ("DELETE", "/sandbox/sb-1") in [(r.method, r.url.path) for r in rec.requests]


def test_upload_excludes_junk_and_preserves_tree(mock_api, workspace):
    rec = mock_api(_Recorder())
    sc.SandboxClient("http://s").upload_workspace("sb-1", workspace)

    upload = next(r for r in rec.requests if r.url.path.endswith("/files"))
    with tarfile.open(fileobj=io.BytesIO(upload.content)) as tar:
        names = {n.lstrip("./") for n in tar.getnames()}
    assert "main.py" in names and "requirements.txt" in names
    assert not any("__pycache__" in n for n in names)


def test_nonzero_exit_is_reported_not_raised(mock_api):
    mock_api(_Recorder(exit_code=1, stdout=("boom",)))
    code, out = sc.SandboxClient("http://s").execute("sb-1", ["false"])
    assert (code, out) == (1, "boom")


def test_create_failure_raises_sandbox_error(mock_api):
    mock_api(_Recorder(create_status=500))
    with pytest.raises(sc.SandboxError):
        sc.SandboxClient("http://s").create()


def test_delete_failure_is_swallowed(monkeypatch):
    def boom(*a, **kw):
        raise httpx.ConnectError("unreachable")

    monkeypatch.setattr(sc.httpx, "delete", boom)
    sc.SandboxClient("http://s").delete("sb-1")  # must not raise


@pytest.mark.parametrize("value,expected", [
    ("http://sandbox:18080/", "http://sandbox:18080"),
    ("  http://sandbox:18080  ", "http://sandbox:18080"),
    ("", None),
    ("   ", None),
])
def test_url_resolution(monkeypatch, value, expected):
    monkeypatch.setenv("SANDBOX_API_URL", value)
    assert sc.resolve_sandbox_api_url() == expected


def test_url_unset(monkeypatch):
    monkeypatch.delenv("SANDBOX_API_URL", raising=False)
    assert sc.resolve_sandbox_api_url() is None


# ── preview sandboxes ────────────────────────────────────────────────────────

def test_create_preview_returns_published_url(mock_api):
    rec = mock_api(_Recorder(preview_url="http://127.0.0.1:38989"))
    sandbox_id, url = sc.SandboxClient("http://s").create_preview(
        image="python:3.12-alpine", expose_port=8000
    )
    assert (sandbox_id, url) == ("sb-1", "http://127.0.0.1:38989")

    create = next(r for r in rec.requests if r.url.path.endswith("/create"))
    assert json.loads(create.content)["expose_port"] == 8000


def test_create_without_expose_port_requests_no_preview(mock_api):
    rec = mock_api(_Recorder())
    sandbox_id, url = sc.SandboxClient("http://s").create_preview()
    assert (sandbox_id, url) == ("sb-1", "")

    create = next(r for r in rec.requests if r.url.path.endswith("/create"))
    assert "expose_port" not in json.loads(create.content)


def test_start_background_flags_the_request(mock_api):
    """A server must not be run through the streaming path — it never returns."""
    rec = mock_api(_Recorder())
    sc.SandboxClient("http://s").start_background("sb-1", ["python3", "-m", "http.server"])

    req = next(r for r in rec.requests if r.url.path.endswith("/execute"))
    assert json.loads(req.content)["background"] is True


def test_background_failure_raises(monkeypatch):
    def boom(*a, **kw):
        raise httpx.ConnectError("unreachable")

    monkeypatch.setattr(sc.httpx, "post", boom)
    with pytest.raises(sc.SandboxError):
        sc.SandboxClient("http://s").start_background("sb-1", ["true"])


# ── smoke-test backend ───────────────────────────────────────────────────────

def test_backend_registered():
    assert test_tools._BACKENDS["sandbox_api"] is test_tools.SandboxAPIBackend


def test_backend_commands_target_workspace_mount():
    """Commands must cd into the upload mount, not the bind-mount path."""
    for cmd in test_tools.SANDBOX_API_COMMANDS.values():
        assert "cd /app" not in cmd
    assert "cd /workspace" in test_tools.SANDBOX_API_COMMANDS["python"]


def test_backend_commands_redirect_home_to_writable_mount():
    """Read-only root: go/npm/pip/maven caches must not target the real $HOME."""
    for cmd in test_tools.SANDBOX_API_COMMANDS.values():
        assert cmd.startswith("export HOME=/workspace/")


def test_backend_passes_on_zero_exit(mock_api, monkeypatch, workspace):
    monkeypatch.setenv("SANDBOX_API_URL", "http://sandbox:18080")
    mock_api(_Recorder())
    result = test_tools.SandboxAPIBackend().run(workspace, "python")
    assert str(result).startswith("✅")


def test_backend_fails_on_nonzero_exit(mock_api, monkeypatch, workspace):
    monkeypatch.setenv("SANDBOX_API_URL", "http://sandbox:18080")
    mock_api(_Recorder(exit_code=1, stdout=("SyntaxError: bad",)))
    result = test_tools.SandboxAPIBackend().run(workspace, "python")
    assert str(result).startswith("❌")
    assert "SyntaxError" in str(result)


def test_backend_errors_clearly_when_url_missing(monkeypatch, workspace):
    """Opting into this backend without a URL must not silently pass."""
    monkeypatch.delenv("SANDBOX_API_URL", raising=False)
    result = test_tools.SandboxAPIBackend().run(workspace, "python")
    assert str(result).startswith("❌")
    assert "SANDBOX_API_URL" in str(result)


def test_backend_surfaces_unreachable_service(monkeypatch, workspace):
    monkeypatch.setenv("SANDBOX_API_URL", "http://sandbox:18080")

    def boom(*a, **kw):
        raise httpx.ConnectError("unreachable")

    monkeypatch.setattr(sc.httpx, "post", boom)
    result = test_tools.SandboxAPIBackend().run(workspace, "python")
    assert str(result).startswith("❌")


# ── static HTML/CSS/JS smoke test ────────────────────────────────────────────
#
# _detect_project_type can return "static" (a bare index.html, no build step),
# but CONTAINER_IMAGES/CONTAINER_COMMANDS had no entry for it, so every static
# job run with SMOKE_TEST_BACKEND=sandbox_api failed smoke_test unconditionally
# with "No container image configured for project type 'static'" — confirmed
# live: two real jobs landed in validation_issues with exactly that message.

def test_static_has_a_container_image():
    assert "static" in test_tools.CONTAINER_IMAGES


def test_static_has_a_container_command():
    assert "static" in test_tools.CONTAINER_COMMANDS


def test_static_sandbox_command_targets_the_upload_mount():
    assert "cd /app" not in test_tools.SANDBOX_API_COMMANDS["static"]
    assert "cd /workspace" in test_tools.SANDBOX_API_COMMANDS["static"]


@pytest.fixture
def static_workspace(tmp_path):
    (tmp_path / "index.html").write_text("<html><body>hi</body></html>", encoding="utf-8")
    return tmp_path


def test_static_smoke_test_passes_when_entry_point_exists(mock_api, monkeypatch, static_workspace):
    monkeypatch.setenv("SANDBOX_API_URL", "http://sandbox:18080")
    mock_api(_Recorder(stdout=("static entry point present: index.html",)))
    result = test_tools.SandboxAPIBackend().run(static_workspace, "static")
    assert str(result).startswith("✅")


def test_static_smoke_test_fails_on_missing_entry_point(mock_api, monkeypatch, static_workspace):
    # The command itself must actually check for the file rather than always
    # exiting 0 — simulate what `test -s index.html` reports when it's absent.
    monkeypatch.setenv("SANDBOX_API_URL", "http://sandbox:18080")
    mock_api(_Recorder(exit_code=1, stdout=()))
    result = test_tools.SandboxAPIBackend().run(static_workspace, "static")
    assert str(result).startswith("❌")


# ── toolchain commands must work under the sandbox's constraints ─────────────
#
# The sandbox root filesystem is read-only and the container user's real home
# (/home/default) is not writable. Every toolchain that caches under $HOME has
# to be redirected somewhere inside the writable workspace mount.

def test_maven_does_not_rely_on_home_for_its_local_repo():
    """
    Maven resolves its local repo from the OS passwd home, NOT $HOME, so
    exporting HOME is not enough — verified live: a real Java job failed with
    "Could not create local repository at /home/default/.m2/repository" on
    every remediation iteration, because that path is on the read-only root.
    The repo must be pinned explicitly with -Dmaven.repo.local.
    """
    cmd = test_tools.CONTAINER_COMMANDS["java_maven"]
    assert "-Dmaven.repo.local=" in cmd


def test_maven_local_repo_path_is_relative_not_absolute():
    """
    Relative so it lands under whichever workspace dir the command cd's into.
    An absolute /app path would break the sandbox variant, which rewrites
    `cd /app` to the upload mount but would leave a hardcoded path untouched.
    """
    cmd = test_tools.CONTAINER_COMMANDS["java_maven"]
    repo_arg = [a for a in cmd.split() if a.startswith("-Dmaven.repo.local=")][0]
    value = repo_arg.split("=", 1)[1]
    assert not value.startswith("/"), f"must be relative, got {value!r}"
    assert "$HOME" not in value, "must not depend on $HOME — Maven ignores it here"


def test_gradle_does_not_rely_on_home_for_its_cache():
    cmd = test_tools.CONTAINER_COMMANDS["java_gradle"]
    assert "--gradle-user-home" in cmd or "-g " in cmd


def test_python_smoke_test_checks_nested_sources():
    """
    `py_compile *.py` only globs the workspace ROOT. A real FastAPI project
    puts its code in app/ or src/, so the glob matched nothing — and `|| true`
    turned that into a PASS. Verified live: a Python job reported exit_code 0
    with output "[Errno 2] No such file or directory: '*.py'", certifying
    nothing while looking green. compileall recurses and exits non-zero on a
    genuine syntax error.
    """
    cmd = test_tools.CONTAINER_COMMANDS["python"]
    assert "compileall" in cmd
    assert "*.py" not in cmd


def test_python_smoke_test_does_not_swallow_failures():
    """`|| true` makes the check incapable of ever failing."""
    cmd = test_tools.CONTAINER_COMMANDS["python"]
    assert "|| true" not in cmd


# ── agent tool ───────────────────────────────────────────────────────────────

def test_tool_absent_without_url(monkeypatch, workspace):
    """No URL configured → don't offer the agent a tool that must fail."""
    monkeypatch.delenv("SANDBOX_API_URL", raising=False)
    assert create_sandbox_tools(str(workspace)) == []


def test_tool_present_with_url(monkeypatch, workspace):
    monkeypatch.setenv("SANDBOX_API_URL", "http://sandbox:18080")
    tools = create_sandbox_tools(str(workspace))
    assert [t.metadata.name for t in tools] == ["sandbox_execute"]


def test_tool_returns_exit_code_and_output(mock_api, monkeypatch, workspace):
    monkeypatch.setenv("SANDBOX_API_URL", "http://sandbox:18080")
    mock_api(_Recorder(exit_code=2, stdout=("assertion failed",)))
    tool = create_sandbox_tools(str(workspace))[0]
    out = str(tool.call(command=["pytest"]))
    assert "exit_code=2" in out and "assertion failed" in out


def test_tool_rejects_empty_command(monkeypatch, workspace):
    monkeypatch.setenv("SANDBOX_API_URL", "http://sandbox:18080")
    tool = create_sandbox_tools(str(workspace))[0]
    assert "❌" in str(tool.call(command=[]))
