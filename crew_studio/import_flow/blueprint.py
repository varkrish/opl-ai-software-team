"""
Import Flask Blueprint
POST /api/jobs/<job_id>/analyze -> start import analysis (tech stack + index); runs in background thread.
"""
import json
import logging
import threading

from flask import Blueprint, jsonify, current_app

logger = logging.getLogger(__name__)

import_bp = Blueprint("import_flow", __name__)


def _get_job_db():
    return current_app.config["JOB_DB"]


def _get_workspace_base():
    return current_app.config.get("WORKSPACE_PATH", "./workspace")


@import_bp.route("/api/jobs/<job_id>/analyze", methods=["POST"])
def start_import_analysis(job_id: str):
    """Run tech-stack detection and indexing for an import-mode job."""
    job_db = _get_job_db()
    job = job_db.get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    meta = job.get("metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except (json.JSONDecodeError, TypeError):
            meta = {}
    if meta.get("job_mode") != "import":
        return jsonify({"error": "Job is not an import-mode job"}), 400

    phase = job.get("current_phase", "")
    if phase != "awaiting_import":
        return jsonify({
            "error": f"Import analysis can only start when current_phase is awaiting_import (got {phase!r}).",
        }), 400

    if job.get("status") == "running":
        return jsonify({"error": "Job is already running"}), 400

    workspace_path = job.get("workspace_path", "")
    from pathlib import Path
    ws = Path(workspace_path)
    if not ws.is_dir():
        ws = Path(_get_workspace_base()) / f"job-{job_id}"
    if not ws.is_dir():
        return jsonify({"error": "Workspace not found"}), 404

    job_db.update_job(
        job_id,
        {"status": "running", "current_phase": "import_analyzing"},
    )
    job_db.update_progress(job_id, "import_analyzing", 0, "Starting import analysis...")

    def _progress(phase: str, pct: int, msg: str = None):
        job_db.update_progress(job_id, phase, pct, msg or "")

    def _run():
        from crew_studio.import_flow.runner import run_import_analysis
        try:
            run_import_analysis(
                job_id=job_id,
                workspace_path=ws,
                job_db=job_db,
                progress_callback=_progress,
                vision=job.get("vision") or "",
            )
        except Exception as e:
            logger.exception("Import analysis failed for job %s: %s", job_id, e)
            job_db.update_job(job_id, {
                "status": "failed",
                "current_phase": "error",
                "error": str(e),
            })

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return jsonify({
        "status": "accepted",
        "message": "Import analysis started",
    }), 202
