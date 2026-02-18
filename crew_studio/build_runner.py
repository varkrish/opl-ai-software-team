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
) -> Dict[str, Any]:
    """
    Run the SoftwareDevWorkflow on the given workspace with the given vision.
    Sets WORKSPACE_PATH/PROJECT_ID, runs workflow, restores env, returns results.
    Caller is responsible for mark_completed/mark_failed based on return value or exception.
    """
    from src.llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow

    error_log_path = workspace_path / "crew_errors.log"
    original_workspace = os.environ.get("WORKSPACE_PATH")
    os.environ["WORKSPACE_PATH"] = str(workspace_path)
    os.environ["PROJECT_ID"] = job_id

    try:
        with open(error_log_path, "a") as f:
            f.write(f"\n{'='*80}\n")
            f.write(f"JOB STARTED - {datetime.now().isoformat()}\n")
            f.write(f"Vision: {vision[:2000]}{'...' if len(vision) > 2000 else ''}\n")
            f.write(f"{'='*80}\n\n")

        progress_callback("initializing", 5, "Initializing workflow...")
        workflow = SoftwareDevWorkflow(
            project_id=job_id,
            workspace_path=workspace_path,
            vision=vision,
            config=config,
            progress_callback=progress_callback,
        )
        progress_callback("meta", 10, "Starting Meta phase...")
        results = workflow.run()

        with open(error_log_path, "a") as f:
            f.write(f"\n{'='*80}\n")
            f.write(f"JOB COMPLETED SUCCESSFULLY - {datetime.now().isoformat()}\n")
            f.write(f"{'='*80}\n\n")
        return results

    except Exception as e:
        error_trace = traceback.format_exc()
        with open(error_log_path, "a") as f:
            f.write(f"\n{'='*80}\n")
            f.write(f"ERROR IN WORKFLOW - {datetime.now().isoformat()}\n")
            f.write(f"{'='*80}\n")
            f.write(f"Error Type: {type(e).__name__}\n")
            f.write(f"Error Message: {str(e)}\n")
            f.write(f"Traceback:\n{error_trace}\n")
            f.write(f"{'='*80}\n\n")
        raise
    finally:
        if original_workspace is not None:
            os.environ["WORKSPACE_PATH"] = original_workspace
        elif "WORKSPACE_PATH" in os.environ:
            del os.environ["WORKSPACE_PATH"]
