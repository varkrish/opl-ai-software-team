"""HTTP client for the standalone Sandbox API code-execution service.

The Sandbox API (https://github.com/varkrish/podman-sandbox-api) runs untrusted
code in hardened, ephemeral containers. This client wraps its four endpoints so
both the smoke-test backend and the agent-facing tool share one implementation.

Configured via ``SANDBOX_API_URL``; presence of that variable is the feature
flag, matching the ``VALIDATOR_URL`` convention used elsewhere in this codebase.
"""
import io
import json
import logging
import os
import tarfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

WORKSPACE_DIR = "/workspace"

# Directories that must never be shipped into a sandbox: caches, VCS metadata,
# and vendored dependencies. Excluding them keeps uploads small and avoids
# leaking host-specific build state into the run.
_EXCLUDED_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".tldr", "target", "dist", "build",
    ".gradle", ".idea", ".vscode",
}


def resolve_sandbox_api_url() -> Optional[str]:
    """Return the configured Sandbox API base URL, or None if unset."""
    url = (os.environ.get("SANDBOX_API_URL") or "").strip()
    return url.rstrip("/") if url else None


def _tar_filter(info: tarfile.TarInfo) -> Optional[tarfile.TarInfo]:
    parts = Path(info.name).parts
    if any(p in _EXCLUDED_DIRS for p in parts):
        return None
    return info


class SandboxError(RuntimeError):
    """Raised when the Sandbox API cannot fulfil a request."""


class SandboxClient:
    """Thin client over the Sandbox API's create/upload/execute/delete endpoints."""

    def __init__(self, base_url: str, timeout: float = 300.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def create(
        self, image: str = "", timeout_seconds: int = 0, expose_port: int = 0
    ) -> str:
        """Create a sandbox and return its id.

        Use :meth:`create_preview` when the published URL is needed.
        """
        return self.create_preview(
            image=image, timeout_seconds=timeout_seconds, expose_port=expose_port
        )[0]

    def create_preview(
        self, image: str = "", timeout_seconds: int = 0, expose_port: int = 0
    ) -> Tuple[str, str]:
        """Create a sandbox, returning ``(sandbox_id, preview_url)``.

        ``preview_url`` is empty unless *expose_port* was requested and the
        server permits preview sandboxes.
        """
        payload = {}
        if image:
            payload["image"] = image
        if timeout_seconds:
            payload["timeout_seconds"] = timeout_seconds
        if expose_port:
            payload["expose_port"] = expose_port
        try:
            resp = httpx.post(
                f"{self.base_url}/sandbox/create", json=payload, timeout=self.timeout
            )
            resp.raise_for_status()
            body = resp.json()
            return body["sandbox_id"], body.get("preview_url", "")
        except Exception as e:
            raise SandboxError(f"create failed: {e}") from e

    def start_background(self, sandbox_id: str, command: List[str]) -> None:
        """Start a long-running process (e.g. a server) without waiting for it."""
        try:
            resp = httpx.post(
                f"{self.base_url}/sandbox/{sandbox_id}/execute",
                json={"command": command, "background": True},
                timeout=self.timeout,
            )
            resp.raise_for_status()
        except Exception as e:
            raise SandboxError(f"background start failed: {e}") from e

    def upload_workspace(self, sandbox_id: str, workspace: Path) -> None:
        """Tar *workspace* in memory and extract it into the sandbox."""
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            # Add children individually rather than the tree root: a "." entry
            # makes tar chmod/utime the mount point itself, which fails in
            # images that run as a non-root user.
            for child in sorted(workspace.iterdir()):
                tar.add(str(child), arcname=child.name, filter=_tar_filter)
        buf.seek(0)
        try:
            resp = httpx.post(
                f"{self.base_url}/sandbox/{sandbox_id}/files",
                content=buf.getvalue(),
                headers={"Content-Type": "application/x-tar"},
                timeout=self.timeout,
            )
            resp.raise_for_status()
        except Exception as e:
            raise SandboxError(f"upload failed: {e}") from e

    def execute(self, sandbox_id: str, command: List[str]) -> Tuple[int, str]:
        """Run *command* and return ``(exit_code, combined_output)``.

        Consumes the SSE stream: ``stdout``/``stderr`` events accumulate into the
        output, and the terminating ``exit`` event carries the process exit code.
        """
        lines: List[str] = []
        exit_code = -1
        try:
            with httpx.stream(
                "POST",
                f"{self.base_url}/sandbox/{sandbox_id}/execute",
                json={"command": command},
                headers={"Accept": "text/event-stream"},
                timeout=self.timeout,
            ) as resp:
                resp.raise_for_status()
                event = ""
                for raw in resp.iter_lines():
                    line = raw.rstrip("\n")
                    if line.startswith("event: "):
                        event = line[len("event: "):].strip()
                    elif line.startswith("data: "):
                        data = line[len("data: "):]
                        if event == "exit":
                            try:
                                exit_code = int(json.loads(data).get("code", -1))
                            except (ValueError, json.JSONDecodeError):
                                exit_code = -1
                        else:
                            lines.append(data)
        except Exception as e:
            raise SandboxError(f"execute failed: {e}") from e
        return exit_code, "\n".join(lines)

    def delete(self, sandbox_id: str) -> None:
        """Best-effort teardown; failures are logged, never raised."""
        try:
            httpx.delete(f"{self.base_url}/sandbox/{sandbox_id}", timeout=30.0)
        except Exception as e:
            logger.warning("Sandbox %s cleanup failed: %s", sandbox_id, e)

    @contextmanager
    def sandbox(self, image: str = "", timeout_seconds: int = 0) -> Iterator[str]:
        """Yield a sandbox id, guaranteeing deletion on exit."""
        sandbox_id = self.create(image=image, timeout_seconds=timeout_seconds)
        try:
            yield sandbox_id
        finally:
            self.delete(sandbox_id)
