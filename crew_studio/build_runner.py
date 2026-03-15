"""
Shared build pipeline runner — single place that runs SoftwareDevWorkflow.

Used by:
- run_job_async (normal job flow)
- refactor blueprint (after refactor success, run build on refactored/)
No duplication of workflow creation or run logic.
"""
import os
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from src.llamaindex_crew.config import SecretConfig


def run_build_pipeline(
    job_id: str,
    workspace_path: Path,
    vision: str,
    config: SecretConfig,
    progress_callback: Callable[[str, int, Optional[str]], None],
    job_db: Any,
    resume: bool = False,
) -> Dict[str, Any]:
    """
    Run the SoftwareDevWorkflow on the given workspace with the given vision.
    Sets thread-local WORKSPACE_PATH for file tools (thread-safe for concurrent jobs).
    Caller is responsible for mark_completed/mark_failed based on return value or exception.
    """
    from src.llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow
    from src.llamaindex_crew.tools.file_tools import set_thread_workspace, clear_thread_workspace

    # Ensure workspace exists (e.g. may have been deleted; resume or retry can pass stale path)
    workspace_path = Path(workspace_path)
    workspace_path.mkdir(parents=True, exist_ok=True)
    error_log_path = workspace_path / "crew_errors.log"

    # Thread-local: each job thread sees its own workspace path in file tools
    set_thread_workspace(str(workspace_path))
    # Also set env var for code that reads os.getenv("WORKSPACE_PATH") directly
    # (e.g. meta_agent.py). Thread-local takes precedence in file_tools.
    original_workspace = os.environ.get("WORKSPACE_PATH")
    os.environ["WORKSPACE_PATH"] = str(workspace_path)
    os.environ["PROJECT_ID"] = job_id

    # Verify file tools will resolve to this workspace (sanity check)
    try:
        from src.llamaindex_crew.tools.file_tools import _resolve_workspace
        resolved = _resolve_workspace()
        if Path(resolved).resolve() != workspace_path.resolve():
            import logging
            logging.getLogger(__name__).warning(
                "build_runner: resolved workspace %s != job workspace %s",
                resolved, workspace_path,
            )
    except Exception:
        pass

    def _append_log(*lines: str) -> None:
        try:
            with open(error_log_path, "a") as f:
                f.write("\n".join(lines) + "\n")
        except OSError:
            pass  # do not mask original error if log file is missing/unwritable

    try:
        _append_log(
            f"\n{'='*80}",
            f"JOB STARTED - {datetime.now().isoformat()}",
            f"Vision: {vision[:2000]}{'...' if len(vision) > 2000 else ''}",
            f"{'='*80}\n",
        )

        progress_callback("initializing", 5, "Initializing workflow...")
        workflow = SoftwareDevWorkflow(
            project_id=job_id,
            workspace_path=workspace_path,
            vision=vision,
            config=config,
            progress_callback=progress_callback,
        )
        progress_callback("meta", 10, "Starting Meta phase...")
        results = workflow.run(resume=resume)

        _append_log(
            f"\n{'='*80}",
            f"JOB COMPLETED SUCCESSFULLY - {datetime.now().isoformat()}",
            f"{'='*80}\n",
        )
        return results

    except Exception as e:
        error_trace = traceback.format_exc()
        _append_log(
            f"\n{'='*80}",
            f"ERROR IN WORKFLOW - {datetime.now().isoformat()}",
            f"{'='*80}",
            f"Error Type: {type(e).__name__}",
            f"Error Message: {str(e)}",
            f"Traceback:\n{error_trace}",
            f"{'='*80}\n",
        )
        raise
    finally:
        clear_thread_workspace()
        if original_workspace is not None:
            os.environ["WORKSPACE_PATH"] = original_workspace
        elif "WORKSPACE_PATH" in os.environ:
            del os.environ["WORKSPACE_PATH"]
