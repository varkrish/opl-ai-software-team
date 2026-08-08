"""Agent-facing tool for executing code in an isolated Sandbox API container."""
import logging
from pathlib import Path
from typing import List, Optional

from llama_index.core.tools import FunctionTool

from ..utils.sandbox_client import (
    WORKSPACE_DIR,
    SandboxClient,
    SandboxError,
    resolve_sandbox_api_url,
)

logger = logging.getLogger(__name__)

_MAX_OUTPUT_CHARS = 4000


def SandboxExecuteTool(
    workspace_path: str,
    service_url: str,
    default_image: str = "",
) -> FunctionTool:
    """Create a FunctionTool that runs a command against the current workspace."""

    def sandbox_execute(command: List[str], image: Optional[str] = None) -> str:
        if not command:
            return "❌ command must be a non-empty list of arguments"

        client = SandboxClient(service_url)
        try:
            with client.sandbox(image=image or default_image) as sandbox_id:
                client.upload_workspace(sandbox_id, Path(workspace_path))
                exit_code, output = client.execute(sandbox_id, command)
        except SandboxError as e:
            logger.warning("Sandbox execution failed: %s", e)
            return f"❌ Sandbox execution failed: {e}"

        truncated = output[:_MAX_OUTPUT_CHARS]
        if len(output) > _MAX_OUTPUT_CHARS:
            truncated += f"\n... (truncated, {len(output)} chars total)"
        status = "✅" if exit_code == 0 else "❌"
        return f"{status} exit_code={exit_code}\n{truncated or '(no output)'}"

    return FunctionTool.from_defaults(
        fn=sandbox_execute,
        name="sandbox_execute",
        description=(
            "Run a command against the current project in an isolated, network-less "
            "container and return its exit code and output. The workspace is uploaded "
            f"to {WORKSPACE_DIR}, which is the only large writable directory — cd there "
            "first (e.g. ['sh', '-c', 'cd /workspace && pytest']). Pass `command` as an "
            "argv list, not a shell string. Use this to verify generated code actually "
            "runs, execute tests, or reproduce an error before fixing it."
        ),
    )


def create_sandbox_tools(workspace_path: str, default_image: str = "") -> List[FunctionTool]:
    """Return sandbox tools if SANDBOX_API_URL is configured, else an empty list."""
    service_url = resolve_sandbox_api_url()
    if not service_url:
        return []
    return [SandboxExecuteTool(workspace_path, service_url, default_image)]
