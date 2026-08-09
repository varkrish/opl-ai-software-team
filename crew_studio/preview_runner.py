"""Live preview: run a job's generated app in a sandbox and expose its URL.

Wraps the Sandbox API's preview mode (a sandbox that publishes a port and keeps
networking on) so a user can click through the app the crew just generated.

State lives in the job's ``metadata.live_preview`` so a preview survives page
reloads and can be stopped from any request handler.
"""
import json
import logging
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger(__name__)

METADATA_KEY = "live_preview"

# Where the app's own stdout/stderr lands inside the sandbox, so a failure to
# come up can be explained instead of surfacing as an empty browser response.
APP_LOG = "/tmp/preview.log"

READINESS_TIMEOUT_SECONDS = 25
READINESS_POLL_SECONDS = 1.0

# Images matching the smoke-test backend, so a preview runs on the same
# toolchain the project was validated against.
PREVIEW_IMAGES = {
    "node": "registry.access.redhat.com/ubi9/nodejs-20:latest",
    "python": "registry.access.redhat.com/ubi9/python-311:latest",
    "java_maven": "registry.access.redhat.com/ubi9/openjdk-21:latest",
    "java_gradle": "registry.access.redhat.com/ubi9/openjdk-21:latest",
    "go": "registry.access.redhat.com/ubi9/go-toolset:latest",
    # Reuses the python image already pulled for "python" — no extra image to
    # fetch just to serve static files.
    "static": "registry.access.redhat.com/ubi9/python-311:latest",
}

DEFAULT_PORT = 8000


class PreviewError(RuntimeError):
    """Raised when a preview cannot be started."""


_PLACEHOLDER_RE = re.compile(r"<[^>]+>|\.\.\.|TODO|FIXME", re.IGNORECASE)
# Paths the command names, e.g. `python3 backend/main.py`, `-r req/base.txt`.
_COMMAND_PATH_RE = re.compile(r"(?<![\w/.-])([\w.-]+(?:/[\w.-]+)+\.[A-Za-z0-9]+)")


def _read_start_command(workspace: Path) -> str:
    """Honour an explicit ``preview_command`` in test_plan.md, if it holds up.

    The model that writes this line is the same one that pinned a nonexistent
    ``memmachine-client==0.1.5`` and emitted ``jq '...'`` where a jq filter was
    required, so a declaration is preferred but not taken on trust: it is
    dropped when it is a placeholder, or when it names a file the project does
    not contain. Falling through to detection beats starting a container that
    cannot work.
    """
    plan = workspace / "test_plan.md"
    if not plan.is_file():
        return ""
    command = ""
    for line in plan.read_text(encoding="utf-8", errors="replace").splitlines():
        key, _, value = line.strip().partition(":")
        if key.strip() == "preview_command":
            command = value.strip()
            break
    if not command or command.lower() in ("none", "n/a", "-"):
        return ""
    if _PLACEHOLDER_RE.search(command):
        logger.warning("Ignoring placeholder preview_command: %r", command)
        return ""
    for referenced in _COMMAND_PATH_RE.findall(command):
        if not (workspace / referenced).exists():
            logger.warning(
                "Ignoring preview_command %r: it names %r, which is not in the "
                "project; falling back to detection",
                command, referenced,
            )
            return ""
    return command


_MAIN_GUARD_RE = re.compile(r"^if\s+__name__\s*==\s*['\"]__main__['\"]", re.M)


_CONVENTIONAL_ENTRYPOINTS = ("main.py", "app.py", "server.py", "run.py", "wsgi.py")

# Directories that never hold the thing to run.
_SKIP_DIRS = frozenset({
    "node_modules", ".venv", "venv", "env", ".git", "__pycache__",
    "site-packages", ".tox", "dist", "build", "target", "out", "vendor",
    "test", "tests", "__tests__", "spec", "specs", "testing",
    ".mypy_cache", ".pytest_cache", "migrations",
})


def _is_skipped(rel: Path) -> bool:
    return any(part in _SKIP_DIRS for part in rel.parts[:-1])


def _python_entrypoint_candidates(workspace: Path) -> List[str]:
    """Every plausible entrypoint, workspace-relative, root-level ones first.

    Used to be root-only, which assumes the project is single-module and
    rooted. Job 107b3d3e put its API in ``backend/main.py`` — the layout its own
    wiring contract declared — and Start Preview answered "No Python entrypoint
    found". ``PythonStrategy.validate_entrypoint`` walks the tree with
    ``rglob`` and passed on the same workspace, so the project was
    simultaneously valid and unpreviewable.
    """
    conventional: List[tuple] = []
    declared: List[str] = []

    for src in sorted(workspace.rglob("*.py")):
        if not src.is_file():
            continue
        try:
            rel = src.relative_to(workspace)
        except ValueError:
            continue
        if _is_skipped(rel):
            continue
        rel_str = rel.as_posix()
        depth = len(rel.parts) - 1
        if src.name in _CONVENTIONAL_ENTRYPOINTS:
            conventional.append((depth, _CONVENTIONAL_ENTRYPOINTS.index(src.name), rel_str))
            continue
        try:
            if _MAIN_GUARD_RE.search(src.read_text(encoding="utf-8", errors="replace")):
                declared.append(rel_str)
        except OSError:
            continue

    if conventional:
        return [rel for _d, _pref, rel in sorted(conventional)]
    return sorted(declared, key=lambda p: (p.count("/"), p))


def _python_entrypoint(workspace: Path) -> Optional[str]:
    """The single file to run, or None when the choice is not obvious.

    A root-level entrypoint wins outright — that is the project's own answer.
    Below the root, only an unambiguous single candidate is accepted: starting
    the wrong half of a two-service project is worse than saying so.
    """
    candidates = _python_entrypoint_candidates(workspace)
    root_level = [c for c in candidates if "/" not in c]
    if root_level:
        return root_level[0]
    return candidates[0] if len(candidates) == 1 else None


def _pip_install_prefix(workspace: Path, entry: str) -> str:
    """Install from the requirements.txt nearest the entrypoint.

    Only ``workspace/requirements.txt`` used to count, so a discovered
    subdirectory entrypoint would start without its dependencies and crash on
    the first import.

    Install output is no longer sent to /dev/null. A failed install used to be
    invisible, surfacing later as an unexplained ImportError from the app —
    while the real cause (on job 107b3d3e, a pin for a version that does not
    exist) sat in the suppressed output.
    """
    entry_dir = Path(entry).parent
    preferred = [entry_dir / "requirements.txt", Path("requirements.txt")]
    for rel in preferred:
        if (workspace / rel).is_file():
            return f"pip install --no-cache-dir -r {rel.as_posix()} 2>&1; "
    for found in sorted(workspace.rglob("requirements.txt")):
        try:
            rel = found.relative_to(workspace)
        except ValueError:
            continue
        if _is_skipped(rel) or not found.is_file():
            continue
        return f"pip install --no-cache-dir -r {rel.as_posix()} >/dev/null 2>&1; "
    return ""


def _node_package_json(workspace: Path) -> Optional[Path]:
    """The package.json that declares how to start the app, root preferred."""
    root = workspace / "package.json"
    if root.is_file():
        return root
    for found in sorted(workspace.rglob("package.json")):
        try:
            rel = found.relative_to(workspace)
        except ValueError:
            continue
        if _is_skipped(rel) or not found.is_file():
            continue
        try:
            scripts = json.loads(found.read_text(encoding="utf-8")).get("scripts", {})
        except (json.JSONDecodeError, OSError, AttributeError):
            continue
        if isinstance(scripts, dict) and ("start" in scripts or "dev" in scripts):
            return found
    return None


def detect_preview(workspace: Path, project_type: str) -> Tuple[str, int]:
    """Return ``(shell_command, container_port)`` to serve the project.

    Generated projects vary too much to detect perfectly; ``preview_command``
    in test_plan.md always wins so a project can state its own.
    """
    explicit = _read_start_command(workspace)
    if explicit:
        return explicit, _port_from_command(explicit) or DEFAULT_PORT

    if project_type == "node":
        pkg = _node_package_json(workspace)
        if pkg is not None:
            try:
                scripts = json.loads(pkg.read_text(encoding="utf-8")).get("scripts", {})
            except (json.JSONDecodeError, OSError):
                scripts = {}
            # cd into the package when it is not at the root, so npm resolves
            # the right manifest.
            rel_dir = pkg.parent.relative_to(workspace).as_posix()
            prefix = "" if rel_dir in ("", ".") else f"cd {rel_dir} && "
            if "start" in scripts:
                return f"{prefix}npm install --ignore-scripts && npm start", DEFAULT_PORT
            if "dev" in scripts:
                return f"{prefix}npm install --ignore-scripts && npm run dev", DEFAULT_PORT
        raise PreviewError("No 'start' or 'dev' script found in package.json")

    if project_type == "python":
        entry = _python_entrypoint(workspace)
        if not entry:
            candidates = _python_entrypoint_candidates(workspace)
            if len(candidates) > 1:
                raise PreviewError(
                    f"Several runnable entrypoints found ({', '.join(candidates)}). "
                    f"Preview runs one process, so add a 'preview_command:' line to "
                    f"test_plan.md naming the one to start."
                )
            raise PreviewError(
                "No Python entrypoint found (looked for "
                f"{', '.join(_CONVENTIONAL_ENTRYPOINTS)} and any file with a "
                "__main__ guard, in the project root and its subdirectories)"
            )
        install = _pip_install_prefix(workspace, entry)
        return f"{install}python3 {entry}", DEFAULT_PORT

    if project_type == "go":
        return "go run ./...", DEFAULT_PORT

    if project_type == "static":
        # http.server binds all interfaces by default (no loopback-only trap)
        # and needs no dependency install — index.html is already confirmed
        # present by _detect_project_type.
        return f"python3 -m http.server {DEFAULT_PORT}", DEFAULT_PORT

    if project_type in ("java_maven", "java_gradle"):
        raise PreviewError(
            f"Live preview is not supported for {project_type} projects yet"
        )

    raise PreviewError(f"Cannot determine how to start a '{project_type}' project")


_PORT_RE = re.compile(r"(?::|--port[= ]|PORT=)(\d{2,5})\b")


def _port_from_command(command: str) -> Optional[int]:
    match = _PORT_RE.search(command)
    if not match:
        return None
    port = int(match.group(1))
    return port if 1 <= port <= 65535 else None


def _job_metadata(job: Dict[str, Any]) -> Dict[str, Any]:
    """Read a job's metadata, which JobDatabase.get_job already decodes to a dict.

    Raw rows elsewhere still carry the JSON string, so both are accepted —
    treating a dict as unparseable would silently discard every existing key.
    """
    raw = job.get("metadata")
    if isinstance(raw, dict):
        return dict(raw)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def probe_url(preview_url: str, api_base_url: str) -> str:
    """Rewrite a host-loopback preview URL so it is reachable from this process.

    ``preview_url`` points at the host's loopback, which is right for the user's
    browser but unreachable from inside a container — reuse whichever host the
    Sandbox API itself is reached on.
    """
    api_host = urlparse(api_base_url).hostname
    if not api_host or api_host in ("127.0.0.1", "localhost", "::1"):
        return preview_url
    parsed = urlparse(preview_url)
    port = parsed.port
    return urlunparse(parsed._replace(netloc=f"{api_host}:{port}" if port else api_host))


def _wait_until_serving(url: str, timeout: int = READINESS_TIMEOUT_SECONDS) -> bool:
    """Poll *url* until it answers. Any HTTP status counts — even 404 proves a
    server is listening; only a refused/empty connection means it is not."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=3)
            return True
        except urllib.error.HTTPError:
            return True
        except Exception:
            time.sleep(READINESS_POLL_SECONDS)
    return False


def _read_app_log(client, sandbox_id: str) -> str:
    """Fetch whatever the app printed before failing to serve."""
    try:
        _, output = client.execute(sandbox_id, ["sh", "-c", f"cat {APP_LOG} 2>/dev/null"])
        return output.strip()
    except Exception:
        return ""


def get_preview_state(job: Dict[str, Any]) -> Dict[str, Any]:
    """Return the stored preview state for *job* (empty dict when none)."""
    state = _job_metadata(job).get(METADATA_KEY)
    return state if isinstance(state, dict) else {}


def _store_preview_state(job_db, job_id: str, state: Optional[Dict[str, Any]]) -> None:
    job = job_db.get_job(job_id)
    if not job:
        return
    metadata = _job_metadata(job)
    if state is None:
        metadata.pop(METADATA_KEY, None)
    else:
        metadata[METADATA_KEY] = state
    job_db.update_job(job_id, {"metadata": json.dumps(metadata)})


def start_preview(job_db, job: Dict[str, Any]) -> Dict[str, Any]:
    """Provision a preview sandbox for *job* and start its app.

    Returns the stored preview state. Raises PreviewError on any failure,
    leaving no sandbox behind.
    """
    from llamaindex_crew.tools.test_tools import _detect_project_type
    from llamaindex_crew.utils.sandbox_client import (
        SandboxClient,
        SandboxError,
        WORKSPACE_DIR,
        resolve_sandbox_api_url,
    )

    base_url = resolve_sandbox_api_url()
    if not base_url:
        raise PreviewError("SANDBOX_API_URL is not configured")

    workspace = Path(job["workspace_path"])
    if not workspace.is_dir():
        raise PreviewError("Job workspace no longer exists")

    project_type = _detect_project_type(workspace)
    image = PREVIEW_IMAGES.get(project_type)
    if not image:
        raise PreviewError(f"Cannot preview project type '{project_type}'")

    command, port = detect_preview(workspace, project_type)

    client = SandboxClient(base_url)
    sandbox_id = ""
    try:
        sandbox_id, preview_url = client.create_preview(image=image, expose_port=port)
        if not preview_url:
            raise PreviewError(
                "Sandbox API did not return a preview URL — is security.allow_preview enabled?"
            )
        client.upload_workspace(sandbox_id, workspace)
        client.start_background(
            sandbox_id,
            ["sh", "-c", f"cd {WORKSPACE_DIR} && {command} >{APP_LOG} 2>&1"],
        )

        # Handing back a URL that answers nothing produces an unexplained empty
        # page in the browser; surface the app's own output instead.
        if not _wait_until_serving(probe_url(preview_url, base_url)):
            log = _read_app_log(client, sandbox_id)
            detail = f"\n\nApp output:\n{log[:2000]}" if log else ""
            raise PreviewError(
                f"The app did not start serving on port {port} within "
                f"{READINESS_TIMEOUT_SECONDS}s. Common causes: it is a CLI program "
                f"rather than a web app; it listens on a different port; or it binds "
                f"127.0.0.1 instead of 0.0.0.0 (a loopback bind is unreachable from "
                f"outside the container). Set 'preview_command:' in test_plan.md to "
                f"control how it starts.{detail}"
            )
    except SandboxError as exc:
        if sandbox_id:
            client.delete(sandbox_id)
        raise PreviewError(str(exc)) from exc
    except PreviewError:
        if sandbox_id:
            client.delete(sandbox_id)
        raise

    state = {
        "sandbox_id": sandbox_id,
        "preview_url": preview_url,
        "port": port,
        "command": command,
        "project_type": project_type,
    }
    _store_preview_state(job_db, job["id"], state)
    logger.info("Live preview started for job %s at %s", job["id"], preview_url)
    return state


def stop_preview(job_db, job: Dict[str, Any]) -> bool:
    """Tear down *job*'s preview sandbox. Returns True if one was running."""
    from llamaindex_crew.utils.sandbox_client import SandboxClient, resolve_sandbox_api_url

    state = get_preview_state(job)
    sandbox_id = state.get("sandbox_id")
    if not sandbox_id:
        return False

    base_url = resolve_sandbox_api_url()
    if base_url:
        try:
            SandboxClient(base_url).delete(sandbox_id)
        except Exception:
            # Teardown is best-effort; the janitor reaps the sandbox anyway.
            # Clearing state matters more — otherwise the UI is stuck showing a
            # preview the user can neither reach nor stop.
            logger.warning("Preview sandbox %s teardown failed", sandbox_id, exc_info=True)
    _store_preview_state(job_db, job["id"], None)
    logger.info("Live preview stopped for job %s", job["id"])
    return True
