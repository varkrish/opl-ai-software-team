"""Append structured tool and pipeline events to workspace/execution.log."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional

_workspace_local = threading.local()

# Keys whose values are truncated before writing (file bodies, diffs, etc.)
_CONTENT_KEYS = frozenset({
    "content", "diff_blocks", "new_content", "patch", "body", "text", "data",
})


def set_thread_workspace(path: str) -> None:
    """Mirror file_tools thread-local workspace for execution.log resolution."""
    _workspace_local.workspace_path = path


def clear_thread_workspace() -> None:
    _workspace_local.workspace_path = None


def _resolve_workspace(workspace_path: Optional[str | Path] = None) -> Optional[Path]:
    if workspace_path is not None:
        return Path(workspace_path)
    thread_ws = getattr(_workspace_local, "workspace_path", None)
    if thread_ws is not None:
        return Path(thread_ws)
    env_ws = os.getenv("WORKSPACE_PATH")
    if env_ws:
        return Path(env_ws)
    return None


def truncate_for_log(
    text: str,
    *,
    limit: int = 500,
    label: str = "content",
) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return f"{text[:limit].strip()}\n... [{label} truncated] ({len(text) - limit} characters)"


def _sanitize_args(args: Dict[str, Any], *, content_limit: int = 200) -> Dict[str, Any]:
    clean: Dict[str, Any] = {}
    for key, value in args.items():
        if isinstance(value, str) and (key in _CONTENT_KEYS or len(value) > content_limit):
            clean[key] = truncate_for_log(value, limit=content_limit, label=key)
        else:
            clean[key] = value
    return clean


def append_execution_log(
    message: str,
    *,
    workspace_path: Optional[str | Path] = None,
) -> None:
    """Append a line or block to workspace/execution.log (best-effort)."""
    ws = _resolve_workspace(workspace_path)
    if ws is None:
        return
    log_path = ws / "execution.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(message.rstrip() + "\n")
    except OSError:
        pass


def log_tool_invocation(
    tool_name: str,
    args: Optional[Dict[str, Any]] = None,
    *,
    result: Optional[str] = None,
    status: str = "ok",
    workspace_path: Optional[str | Path] = None,
) -> None:
    """Record a direct tool call (ReAct callback or native FunctionTool)."""
    args = args or {}
    clean_args = _sanitize_args(args)
    block = (
        f"=== [Tool: {tool_name}] status={status} ===\n"
        f"Arguments: {json.dumps(clean_args, indent=2, default=str)}\n"
    )
    if result is not None:
        preview = truncate_for_log(str(result), limit=800, label="result")
        block += f"Result: {preview}\n"
    block += "========================\n"
    append_execution_log(block, workspace_path=workspace_path)


def log_pipeline_event(
    event: str,
    detail: str = "",
    *,
    workspace_path: Optional[str | Path] = None,
) -> None:
    """Record workflow phases, simple-mode writes, validation, etc."""
    line = f"=== [Pipeline: {event}] ==="
    if detail:
        line += f"\n{detail}"
    line += "\n====================\n"
    append_execution_log(line, workspace_path=workspace_path)


def log_llm_error(
    status_code: int,
    error_body: str,
    *,
    model: str = "",
    attempt: int = 0,
    workspace_path: Optional[str | Path] = None,
) -> None:
    """Record an LLM API error (4xx/5xx/timeout) into execution.log.

    Surfaces errors like the Vertex AI Harmony tokens 400 without requiring
    users to dig through raw container logs.
    """
    import datetime
    ts = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    preview = truncate_for_log(error_body, limit=600, label="error_body")
    block = (
        f"=== [LLM Error] ts={ts} status={status_code} model={model} attempt={attempt} ===\n"
        f"{preview}\n"
        f"====================\n"
    )
    append_execution_log(block, workspace_path=workspace_path)
