"""
Read a project's own ``dev-compose.yaml`` to decide how to preview it.

Preview used to reason from filenames — is there a main.py, is there a
package.json — and job 107b3d3e showed the limit of that: "No Python entrypoint
found" for a project whose entrypoint was one directory down. That same job had
already written a compose file naming both services, their ports and their
commands. The answer was on disk and nothing read it.

``dev-compose.yaml`` is the DevOps agent's dev-time declaration: no image
builds, source mounted, install-and-run in the command, and an ``x-preview``
marker naming the service whose port is the URL a human opens. Reading it beats
guessing, and it is the same "declared, not inferred" move as ``preview_command``
one level up.

Verified, not trusted. The model that writes this file is the one that pinned a
nonexistent ``memmachine-client==0.1.5`` on this job, so a compose file that
mounts a directory the project does not have, omits its primary service, binds
loopback, or wants an image build is rejected in favour of the next layer down.
A rejected file costs one fallback. An accepted bad one costs a container that
cannot serve and a user staring at a dead URL.

Today this drives the existing single-process preview by extracting the primary
service. Running the whole compose project needs the Sandbox API to grow a
compose mode, and two things constrain that design:

  * The backend and the Sandbox API share no filesystem — the backend is
    containerised with the workspace in a volume, the API runs on the host,
    which is why UploadFiles tars the workspace in. Mounting source therefore
    means a per-project named volume populated from that upload, not a host
    bind mount.
  * Compose creates its own networks. Sandboxes are deliberately placed on an
    internal network with no default route so the egress allowlist proxy cannot
    be bypassed; a compose project left on its own network would quietly regain
    unrestricted egress.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEV_COMPOSE_FILENAMES = ("dev-compose.yaml", "dev-compose.yml")

# A server bound to loopback inside a container is unreachable from outside it,
# which presents as a preview URL that never responds.
_LOOPBACK_RE = re.compile(r"(?:--host[= ]|host=)['\"]?(?:127\.0\.0\.1|localhost)\b")


@dataclass
class ComposePreview:
    """The one service a preview should start, taken from the project's own file."""

    service: str
    command: str
    port: int
    workdir: str = ""
    services: List[str] = field(default_factory=list)


def _load(path: Path) -> Optional[dict]:
    try:
        import yaml  # noqa: PLC0415 — optional dependency; preview must survive its absence
    except ModuleNotFoundError:
        logger.debug("PyYAML unavailable; skipping dev-compose preview")
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:  # noqa: BLE001 — a bad file must never break preview
        logger.warning("Ignoring unparseable %s: %s", path.name, exc)
        return None
    return data if isinstance(data, dict) else None


def _first_published_port(service: Dict[str, Any]) -> Optional[int]:
    """Container-side port of the first published mapping."""
    for entry in service.get("ports") or []:
        text = str(entry).strip().strip("'\"")
        if not text:
            continue
        # "3000:3000", "127.0.0.1:8080:80", "8000"
        parts = [p for p in text.split("/")[0].split(":") if p]
        if not parts:
            continue
        candidate = parts[-1]
        if candidate.isdigit():
            port = int(candidate)
            if 1 <= port <= 65535:
                return port
    return None


def _mounted_source_dir(service: Dict[str, Any]) -> Optional[str]:
    """Workspace-relative host side of the first bind mount, if any."""
    for entry in service.get("volumes") or []:
        if isinstance(entry, dict):
            source = str(entry.get("source") or "")
        else:
            source = str(entry).split(":")[0]
        source = source.strip()
        if not source or not source.startswith((".", "/")) and ":" not in str(entry):
            # A named volume, not a bind mount.
            continue
        if source.startswith("./"):
            source = source[2:]
        if source in ("", ".", "/"):
            return ""
        if source.startswith("/"):
            continue  # absolute host path; not workspace-relative
        return source.rstrip("/")
    return None


def _command_text(service: Dict[str, Any]) -> str:
    command = service.get("command")
    if isinstance(command, str):
        return command.strip()
    if isinstance(command, list):
        return " ".join(str(part) for part in command).strip()
    return ""


def read_dev_compose_preview(workspace: Path) -> Optional[ComposePreview]:
    """Return the primary service to preview, or None when the file cannot be trusted.

    Never raises: every rejection is a log line and a fall-through to the next
    detection layer.
    """
    workspace = Path(workspace)
    path = next(
        (workspace / name for name in DEV_COMPOSE_FILENAMES if (workspace / name).is_file()),
        None,
    )
    if path is None:
        return None

    data = _load(path)
    if not data:
        return None

    services = data.get("services")
    if not isinstance(services, dict) or not services:
        logger.warning("Ignoring %s: no services declared", path.name)
        return None

    names = sorted(str(n) for n in services)
    marker = data.get("x-preview")
    primary = ""
    if isinstance(marker, dict):
        primary = str(marker.get("primary") or "").strip()

    if not primary:
        if len(names) == 1:
            primary = names[0]
        else:
            logger.warning(
                "Ignoring %s: %d services and no x-preview.primary, so which one "
                "to open is a guess", path.name, len(names),
            )
            return None

    service = services.get(primary)
    if not isinstance(service, dict):
        logger.warning(
            "Ignoring %s: x-preview.primary is %r, which is not one of %s",
            path.name, primary, names,
        )
        return None

    if service.get("build"):
        logger.warning(
            "Ignoring %s: service %r builds an image; dev-compose must run the "
            "mounted source so preview does not pay a build", path.name, primary,
        )
        return None

    command = _command_text(service)
    if not command:
        logger.warning("Ignoring %s: service %r declares no command", path.name, primary)
        return None
    if _LOOPBACK_RE.search(command):
        logger.warning(
            "Ignoring %s: service %r binds loopback, which is unreachable from "
            "outside the container", path.name, primary,
        )
        return None

    port = _first_published_port(service)
    if not port:
        logger.warning(
            "Ignoring %s: service %r publishes no port, so there is no URL to open",
            path.name, primary,
        )
        return None

    # Every bind mount must point at something the project actually contains —
    # a mount of a missing directory yields an empty container, not an error.
    for name in names:
        entry = services.get(name)
        if not isinstance(entry, dict):
            continue
        source = _mounted_source_dir(entry)
        if source and not (workspace / source).exists():
            logger.warning(
                "Ignoring %s: service %r mounts %r, which is not in the project",
                path.name, name, source,
            )
            return None

    workdir = _mounted_source_dir(service) or ""
    return ComposePreview(
        service=primary,
        command=command,
        port=port,
        workdir=workdir,
        services=names,
    )


_SH_WRAPPER_RE = re.compile(r"""^(?:/bin/)?(?:sh|bash)\s+-c\s+(['"])(?P<body>.*)\1\s*$""", re.DOTALL)


def compose_preview_command(preview: ComposePreview) -> str:
    """Flatten a compose service into one shell command for the sandbox.

    The sandbox runs a single process today, so ``sh -c "..."`` is unwrapped and
    the mounted directory becomes a ``cd`` — running ``npm install`` from the
    wrong directory installs the wrong package.json.
    """
    body = preview.command
    match = _SH_WRAPPER_RE.match(body)
    if match:
        body = match.group("body").strip()
    if preview.workdir:
        return f"cd {preview.workdir} && {body}"
    return body
