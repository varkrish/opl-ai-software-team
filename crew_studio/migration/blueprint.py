"""
Migration Flask Blueprint — self-contained API endpoints for MTA migration.
Register in the main app with: app.register_blueprint(migration_bp)
"""
import json
import logging
import threading
import uuid
from pathlib import Path

from flask import Blueprint, request, jsonify, current_app

try:
    import git as gitpython
except ImportError:
    gitpython = None

logger = logging.getLogger(__name__)

migration_bp = Blueprint("migration", __name__)


def _get_job_db():
    """Get the shared job_db from app config (set during registration)."""
    return current_app.config["JOB_DB"]


def _get_workspace_base():
    """Get the base workspace path from app config."""
    return Path(current_app.config.get("WORKSPACE_PATH", "./workspace"))


# ── POST /api/jobs/<job_id>/migrate ──────────────────────────────────────────

@migration_bp.route("/api/jobs/<job_id>/migrate", methods=["POST"])
def start_migration(job_id: str):
    """Start an MTA migration for an existing job."""
    job_db = _get_job_db()
    job = job_db.get_job(job_id)

    if not job:
        return jsonify({"error": "Job not found"}), 404

    if job["status"] == "running":
        return jsonify({"error": "Job is currently running"}), 400

    # Check if a migration is already running
    running = job_db.get_running_migration(job_id)
    if running:
        return jsonify({"error": "A migration is already in progress"}), 409

    # Parse request
    data = request.get_json(silent=True) or {}
    migration_goal = data.get("migration_goal", "").strip() or "Analyse the MTA report and apply all migration changes"
    migration_notes = data.get("migration_notes", "").strip() or None

    # Find MTA report in uploaded docs
    docs = job_db.get_job_documents(job_id)
    if not docs:
        return jsonify({"error": "No documents uploaded. Please upload the MTA report first."}), 400

    # Use the first uploaded doc as the MTA report (or find one with 'mta' in name)
    report_doc = None
    for doc in docs:
        name_lower = doc["original_name"].lower()
        if any(kw in name_lower for kw in ("mta", "migration", "report", "analysis")):
            report_doc = doc
            break
    if not report_doc:
        report_doc = docs[0]  # Fallback to first doc

    # Resolve workspace
    workspace_path = job.get("workspace_path", "")
    ws = Path(workspace_path)
    if not ws.is_dir():
        ws = _get_workspace_base() / f"job-{job_id}"
    if not ws.is_dir():
        return jsonify({"error": "Workspace not found"}), 404

    # Compute relative path of report inside workspace
    report_stored = Path(report_doc["stored_path"])
    try:
        report_rel = str(report_stored.relative_to(ws))
    except ValueError:
        report_rel = report_doc["stored_path"]

    # Update job status
    job_db.update_job(job_id, {"status": "running", "current_phase": "migrating"})

    migration_id = f"mig-{uuid.uuid4().hex[:12]}"

    def _progress_callback(phase, pct, msg):
        job_db.update_progress(job_id, phase, pct, msg)

    def _run_in_thread():
        from crew_studio.migration.runner import run_migration
        try:
            run_migration(
                job_id=job_id,
                workspace_path=str(ws),
                migration_goal=migration_goal,
                report_path=report_rel,
                migration_notes=migration_notes,
                job_db=job_db,
                progress_callback=_progress_callback,
            )
            # If any migration issues failed, mark job as failed so it's clear something went wrong
            failed = job_db.get_failed_migration_issues(job_id)
            if failed:
                failed_count = len(failed)
                sample_error = failed[0].get("error") or "Unknown"
                job_db.update_job(job_id, {
                    "status": "failed",
                    "current_phase": "migration_failed",
                    "error": f"{failed_count} migration task(s) failed. Example: {sample_error[:400]}",
                })
            else:
                job_db.update_job(job_id, {
                    "status": "completed",
                    "current_phase": "completed",
                })
        except Exception as e:
            logger.error("Migration thread failed: %s", e)
            job_db.update_job(job_id, {
                "status": "failed",
                "current_phase": "migration_failed",
                "error": str(e)[:1000],
            })

    thread = threading.Thread(target=_run_in_thread, daemon=True)
    thread.start()

    return jsonify({
        "status": "migrating",
        "message": "Migration started",
        "migration_id": migration_id,
    }), 202


# ── GET /api/jobs/<job_id>/migration ─────────────────────────────────────────

@migration_bp.route("/api/jobs/<job_id>/migration", methods=["GET"])
def get_migration_status(job_id: str):
    """Get migration summary and issues list."""
    job_db = _get_job_db()
    job = job_db.get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    summary = job_db.get_migration_summary(job_id)
    issues = job_db.get_migration_issues(job_id)

    return jsonify({
        "job_id": job_id,
        "summary": summary,
        "issues": issues,
    })


# ── GET /api/jobs/<job_id>/migration/plan ────────────────────────────────────

@migration_bp.route("/api/jobs/<job_id>/migration/plan", methods=["GET"])
def get_migration_plan(job_id: str):
    """Return the raw migration_plan.json if it exists."""
    job_db = _get_job_db()
    job = job_db.get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    workspace_path = job.get("workspace_path", "")
    ws = Path(workspace_path)
    if not ws.is_dir():
        ws = _get_workspace_base() / f"job-{job_id}"

    plan_path = ws / "migration_plan.json"
    if not plan_path.is_file():
        return jsonify({"error": "Migration plan not found. Run migration first."}), 404

    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        return jsonify(plan)
    except Exception as e:
        return jsonify({"error": f"Failed to read plan: {e}"}), 500


# ── GET /api/jobs/<job_id>/migration/changes ─────────────────────────────────

@migration_bp.route("/api/jobs/<job_id>/migration/changes", methods=["GET"])
def get_migration_changes(job_id: str):
    """Return a summary of file changes made during migration (git diff)."""
    job_db = _get_job_db()
    job = job_db.get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    if not gitpython:
        return jsonify({"error": "gitpython not installed"}), 500

    workspace_path = job.get("workspace_path", "")
    ws = Path(workspace_path)
    if not ws.is_dir():
        ws = _get_workspace_base() / f"job-{job_id}"
    if not (ws / ".git").exists():
        return jsonify({"error": "No git history in workspace"}), 404

    try:
        repo = gitpython.Repo(ws)
        commits = list(repo.iter_commits())

        # Find the "pre-migration snapshot" commit
        pre_commit = None
        for c in commits:
            if "pre-migration" in c.message.lower():
                pre_commit = c
                break

        if not pre_commit:
            # Fallback: use the very first commit as the baseline
            pre_commit = commits[-1] if commits else None

        if not pre_commit:
            return jsonify({"error": "No baseline commit found"}), 404

        # Diff from pre-migration to HEAD
        head_commit = repo.head.commit
        diff_index = pre_commit.diff(head_commit, create_patch=False)

        files_changed = []
        total_insertions = 0
        total_deletions = 0

        for diff_item in diff_index:
            file_path = diff_item.b_path or diff_item.a_path
            change_type = diff_item.change_type  # A, M, D, R, etc.

            # Get per-file stats via numstat
            insertions = 0
            deletions = 0

            files_changed.append({
                "path": file_path,
                "change_type": change_type,
                "insertions": insertions,
                "deletions": deletions,
            })

        # Use git diff --stat for accurate line counts
        try:
            stat_output = repo.git.diff(
                pre_commit.hexsha, head_commit.hexsha, stat=True, numstat=True
            )
            # numstat format: insertions\tdeletions\tfilepath
            stat_lookup = {}
            for line in stat_output.strip().split("\n"):
                parts = line.split("\t")
                if len(parts) == 3:
                    try:
                        ins = int(parts[0]) if parts[0] != "-" else 0
                        dels = int(parts[1]) if parts[1] != "-" else 0
                        stat_lookup[parts[2]] = (ins, dels)
                    except ValueError:
                        pass

            # Merge stats into files list
            for f in files_changed:
                if f["path"] in stat_lookup:
                    f["insertions"], f["deletions"] = stat_lookup[f["path"]]
                total_insertions += f["insertions"]
                total_deletions += f["deletions"]
        except Exception as e:
            logger.warning("Failed to get numstat: %s", e)

        return jsonify({
            "job_id": job_id,
            "baseline_commit": pre_commit.hexsha[:7],
            "head_commit": head_commit.hexsha[:7],
            "total_files": len(files_changed),
            "total_insertions": total_insertions,
            "total_deletions": total_deletions,
            "files": sorted(files_changed, key=lambda f: f["path"]),
        })

    except Exception as e:
        logger.error("Failed to compute migration changes: %s", e)
        return jsonify({"error": f"Failed to compute changes: {e}"}), 500
