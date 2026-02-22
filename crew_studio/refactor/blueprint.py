"""
Refactor Flask Blueprint
POST /api/jobs/<job_id>/refactor -> start refactor (target_stack required); runs in background thread.
GET  /api/jobs/<job_id>/refactor/plan -> returns refactor_plan.json from workspace.
"""
import json
import logging
import threading
from pathlib import Path

from flask import Blueprint, request, jsonify, current_app

logger = logging.getLogger(__name__)

refactor_bp = Blueprint("refactor", __name__)


def _get_job_db():
    return current_app.config["JOB_DB"]

def _get_workspace_base():
    return Path(current_app.config.get("WORKSPACE_PATH", "./workspace"))

@refactor_bp.route("/api/jobs/<job_id>/refactor", methods=["POST"])
def start_refactor(job_id: str):
    """Start a Refactor job."""
    job_db = _get_job_db()
    job = job_db.get_job(job_id)
    
    if not job:
        return jsonify({"error": "Job not found"}), 404
        
    if job["status"] == "running":
        return jsonify({"error": "Job is currently running"}), 400
        
    # Get params
    data = request.get_json(silent=True) or {}
    target_stack = data.get("target_stack", "").strip()
    devops_instructions = data.get("devops_instructions", "").strip()

    if not target_stack:
        return jsonify({"error": "Target stack is required"}), 400
        
    # Resolve workspace
    workspace_path = job.get("workspace_path", "")
    ws = Path(workspace_path)
    if not ws.is_dir():
        ws = _get_workspace_base() / f"job-{job_id}"
        
    if not ws.is_dir():
        return jsonify({"error": "Workspace not found"}), 404
        
    # Assume source is in workspace root (extracted there)
    source_path = str(ws)
    
    # Update status
    job_db.update_job(job_id, {"status": "running", "current_phase": "refactoring"})
    
    def _progress_callback(phase, pct, msg):
        job_db.update_progress(job_id, phase, pct, msg)
        
    def _run_in_thread():
        from crew_studio.refactor.runner import run_refactor_job
        from crew_studio.build_runner import run_build_pipeline
        from src.llamaindex_crew.config import ConfigLoader

        try:
            run_refactor_job(
                job_id=job_id,
                workspace_path=str(ws),
                source_path=source_path,
                target_stack=target_stack,
                tech_preferences=data.get("tech_preferences", ""),
                devops_instructions=devops_instructions,
                job_db=job_db,
                progress_callback=_progress_callback
            )
            refactored_dir = ws / "refactored"
            vision = (
                "Implement and complete the application according to target_architecture.md "
                "and refactor_plan.json in this workspace. Ensure all tasks are implemented "
                "and the application builds and runs."
            )
            if (refactored_dir / "target_architecture.md").exists():
                vision += "\n\n--- target_architecture.md ---\n" + (
                    refactored_dir / "target_architecture.md"
                ).read_text(errors="replace")[:50_000]
            if (refactored_dir / "refactor_plan.json").exists():
                vision += "\n\n--- refactor_plan.json ---\n" + (
                    refactored_dir / "refactor_plan.json"
                ).read_text(errors="replace")[:20_000]

            job_config = ConfigLoader.load()
            results = run_build_pipeline(
                job_id=job_id,
                workspace_path=refactored_dir,
                vision=vision,
                config=job_config,
                progress_callback=_progress_callback,
                job_db=job_db,
            )
            task_validation = results.get("task_validation", {})
            if task_validation.get("valid", True):
                job_db.mark_completed(job_id, {
                    "status": results.get("status", "completed"),
                    "budget_report": results.get("budget_report", {}),
                    "task_validation": task_validation,
                })
            else:
                incomplete = task_validation.get("incomplete_tasks", [])
                failed = task_validation.get("failed_tasks", [])
                err = "Task validation failed.\n"
                if incomplete:
                    err += f"\nIncomplete tasks: {', '.join(incomplete)}"
                if failed:
                    err += f"\nFailed tasks: {', '.join(failed)}"
                job_db.mark_failed(job_id, err)
        except Exception as e:
            logger.error(f"Refactor thread failed: {e}")
            job_db.update_job(job_id, {
                "status": "failed",
                "current_phase": "refactor_failed",
                "error": str(e)
            })
            
    thread = threading.Thread(target=_run_in_thread, daemon=True)
    thread.start()
    
    return jsonify({
        "status": "started",
        "message": "Refactor job started"
    }), 202

@refactor_bp.route("/api/jobs/<job_id>/refactor/plan", methods=["GET"])
def get_refactor_plan(job_id: str):
    """Get the generated refactor plan.

    The plan lives under ``refactored/refactor_plan.json`` in the job
    workspace (the runner writes all output to a ``refactored/`` subdir so
    the original source is preserved).  For backwards compatibility we also
    fall back to the workspace root if the ``refactored/`` copy is absent.
    """
    job_db = _get_job_db()
    job = job_db.get_job(job_id)
    
    if not job:
        return jsonify({"error": "Job not found"}), 404
        
    workspace_path = job.get("workspace_path", "")
    ws = Path(workspace_path)

    # Primary location: refactored subdirectory
    plan_path = ws / "refactored" / "refactor_plan.json"

    # Fallback: workspace root (pre-subdirectory runs)
    if not plan_path.exists():
        plan_path = ws / "refactor_plan.json"

    if not plan_path.exists():
        return jsonify({"error": "Plan not found"}), 404
        
    try:
        plan = json.loads(plan_path.read_text())
        return jsonify(plan)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@refactor_bp.route("/api/jobs/<job_id>/refactor", methods=["GET"])
def get_refactor_status(job_id: str):
    """Get the status of a Refactor job, including task breakdown."""
    job_db = _get_job_db()
    job = job_db.get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
        
    tasks = job_db.get_refactor_tasks(job_id)
    summary = job_db.get_refactor_summary(job_id)
    
    return jsonify({
        "job_id": job_id,
        "summary": summary,
        "tasks": tasks
    })
