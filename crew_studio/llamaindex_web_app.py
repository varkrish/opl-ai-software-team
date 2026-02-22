"""
Web GUI Application for AI Software Development Crew (LlamaIndex)
Provides a web interface to trigger and monitor build jobs
"""
import fnmatch
import io
import os
import json
import uuid
import zipfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional
from urllib.parse import unquote
from flask import Flask, render_template, request, jsonify, send_from_directory, send_file
from flask_cors import CORS
from dotenv import load_dotenv

from src.llamaindex_crew.config import ConfigLoader, SecretConfig
from crew_studio.job_database import JobDatabase

# Load environment variables
load_dotenv()

# Load secure configuration at startup
try:
    config = ConfigLoader.load()
    print(f"✅ Configuration loaded successfully for web app")
except Exception as e:
    print(f"❌ Failed to load configuration: {e}")
    print("Please provide valid configuration via config file or environment variables")
    config = None

# Get the directory of this file
current_dir = Path(__file__).parent
web_dir = current_dir

app = Flask(__name__, 
            static_folder=str(web_dir / 'static') if (web_dir / 'static').exists() else None,
            template_folder=str(web_dir / 'templates') if (web_dir / 'templates').exists() else None)
CORS(app)

# Centralized SQLite database for persistent job storage
db_path = Path(os.getenv("JOB_DB_PATH", "./crew_jobs.db"))
job_db = JobDatabase(db_path)
print(f"✅ Job database initialized at: {db_path.absolute()}")

# Base workspace path (contains job-specific folders)
base_workspace_path = Path(os.getenv("WORKSPACE_PATH", "./workspace"))
base_workspace_path.mkdir(parents=True, exist_ok=True)

# ── Register migration blueprint ─────────────────────────────────────────
from crew_studio.migration.blueprint import migration_bp  # noqa: E402
from crew_studio.refactor.blueprint import refactor_bp  # noqa: E402
app.config["JOB_DB"] = job_db
app.config["WORKSPACE_PATH"] = str(base_workspace_path)
app.register_blueprint(migration_bp)
app.register_blueprint(refactor_bp)


# ── Phases used by migration / refactor / refinement flows ────────────────
# Used by resume_pending_jobs to classify interrupted jobs.
_MIGRATION_PHASES = frozenset({'migrating'})
_REFACTOR_PHASES = frozenset({'refactoring', 'analysis', 'design', 'planning', 'execution', 'devops'})
_REFINEMENT_PHASES = frozenset({'refining'})
_SKIP_PHASES = frozenset({'awaiting_migration', 'awaiting_refactor'})


def resume_pending_jobs(override_job_db=None):
    """Resume or clean up jobs that were in-flight when the server last stopped.

    Called at startup.  For each non-terminal job:
    - Build jobs (queued/running, not awaiting_*): spawn run_job_async thread.
    - Migration/refactor jobs (running): mark as failed (no checkpoint resume).
    - Jobs with a running refinement: fail the refinement, restore job to completed.
    """
    import logging
    _job_db = override_job_db or job_db
    logger = logging.getLogger(__name__)
    logger.info("resume_pending_jobs: scanning for interrupted / pending jobs...")

    all_jobs = _job_db.get_all_jobs()
    resumed = 0
    interrupted = 0

    # 1. Fail any globally-stuck refinements
    for j in all_jobs:
        ref = _job_db.get_running_refinement(j["id"])
        if ref:
            _job_db.fail_refinement(ref["id"], "Interrupted by server restart.")
            _job_db.update_job(j["id"], {
                "status": "completed",
                "current_phase": "completed",
                "progress": 100,
                "error": None,
            })
            interrupted += 1
            logger.info("  Marked stuck refinement %s (job %s) as failed.", ref["id"][:8], j["id"][:8])

    # Re-fetch after refinement cleanup (status may have changed)
    all_jobs = _job_db.get_all_jobs()

    for j in all_jobs:
        status = j.get("status")
        phase = j.get("current_phase", "")

        # Skip terminal states
        if status in ("completed", "failed", "cancelled", "quota_exhausted"):
            continue

        # Skip jobs waiting for explicit user trigger
        if phase in _SKIP_PHASES:
            continue

        # Migration / refactor in progress → mark interrupted (no checkpoint resume)
        if phase in _MIGRATION_PHASES | _REFACTOR_PHASES:
            _job_db.update_job(j["id"], {
                "status": "failed",
                "current_phase": "error",
                "error": "Interrupted by server restart. Please trigger migration/refactor again.",
                "completed_at": None,
            })
            # Also clear any stale migration_issues left in 'running' state
            if phase in _MIGRATION_PHASES:
                _job_db.fail_stale_migrations(j["id"])
            interrupted += 1
            logger.info("  Marked interrupted %s job %s (phase=%s).", "migration" if phase in _MIGRATION_PHASES else "refactor", j["id"][:8], phase)
            continue

        # Resumable build job (queued or running, build phase)
        if status in ("running", "queued"):
            vision = j.get("vision", "")
            logger.info("  Resuming build job %s (status=%s, phase=%s).", j["id"][:8], status, phase)
            thread = threading.Thread(
                target=run_job_async,
                args=(j["id"], vision, config),
                daemon=True,
            )
            thread.start()
            resumed += 1

    logger.info("resume_pending_jobs: %d resumed, %d marked interrupted.", resumed, interrupted)


def run_job_with_backend(job_id: str, vision: str, backend):
    """Run a job using the pluggable backend."""
    import traceback
    import logging
    logger = logging.getLogger(__name__)
    
    def progress_callback(phase: str, progress: int, message: str = None):
        """Update job progress in real-time."""
        job_db.update_progress(job_id, phase, progress, message)
    
    try:
        # Mark job as started
        job_db.mark_started(job_id)
        
        # Get job workspace
        job = job_db.get_job(job_id)
        if not job:
            logger.error(f"Job {job_id} not found in database during backend run")
            return
        
        job_workspace = Path(job['workspace_path'])
        
        # Run the backend
        result = backend.run(job_id, vision, job_workspace, progress_callback)
        
        # Mark as completed or failed based on result
        if result.get('status') == 'success':
            job_db.mark_completed(job_id, result)
        else:
            error = result.get('error', 'Unknown error')
            job_db.mark_failed(job_id, error)
            
    except Exception as e:
        error_msg = f"Backend execution failed: {str(e)}\n{traceback.format_exc()}"
        logger.error(error_msg)
        job_db.mark_failed(job_id, error_msg)


def run_job_async(job_id: str, vision: str, job_config: SecretConfig = None):
    """Run workflow in a separate thread with job-specific workspace"""
    import traceback
    import logging
    
    logger = logging.getLogger(__name__)
    
    # Use global config if not provided
    if job_config is None:
        job_config = config
    
    def progress_callback(phase: str, progress: int, message: str = None):
        """Update job progress in real-time"""
        job_db.update_progress(job_id, phase, progress, message)
    
    try:
        # ── Guard: Skip build pipeline for migration jobs ───────────────
        # Check BEFORE mark_started() because mark_started overwrites
        # current_phase to 'initializing'.
        job = job_db.get_job(job_id)
        if not job:
            logger.error(f"Job {job_id} not found in database during async run")
            return
        
        if job.get("current_phase") == "awaiting_migration":
            logger.warning(
                f"Job {job_id} is a migration job (current_phase=awaiting_migration) — "
                "skipping build pipeline. Use POST /api/jobs/{job_id}/migrate to start migration."
            )
            return
        
        if job.get("current_phase") == "awaiting_refactor":
            logger.warning(
                f"Job {job_id} is a refactor job (current_phase=awaiting_refactor) — "
                "skipping build pipeline. Use POST /api/jobs/{job_id}/refactor to start."
            )
            return
        
        # Mark job as started (sets current_phase='initializing')
        job_db.mark_started(job_id)
        
        # Re-fetch job after mark_started to get updated workspace_path etc.
        job = job_db.get_job(job_id)
        if not job:
            logger.error(f"Job {job_id} not found after mark_started")
            return
        
        from crew_studio.build_runner import run_build_pipeline

        job_workspace = Path(job['workspace_path'])

        # Build enriched vision with uploaded reference docs & GitHub repos
        enriched_vision = vision
        uploaded_docs = job_db.get_job_documents(job_id)
        if uploaded_docs:
            doc_context_parts = []
            repo_context_parts = []
            TEXT_TYPES = (
                'txt', 'md', 'json', 'yaml', 'yml', 'csv', 'xml',
                'py', 'js', 'ts', 'java', 'go', 'rs', 'rb', 'sh',
                'html', 'css', 'sql', 'proto', 'graphql',
            )
            for doc in uploaded_docs:
                doc_path = Path(doc['stored_path'])
                if not doc_path.exists() or doc['file_type'] not in TEXT_TYPES:
                    continue
                try:
                    is_repomix = doc['original_name'].startswith('github:')
                    # Repomix outputs can be large – allow up to 200KB
                    max_chars = 200_000 if is_repomix else 50_000
                    content = doc_path.read_text(errors='replace')[:max_chars]
                    if is_repomix:
                        repo_name = doc['original_name'].replace('github:', '')
                        repo_context_parts.append(
                            f"--- Reference Repository: {repo_name} (packed by Repomix) ---\n{content}"
                        )
                    else:
                        doc_context_parts.append(
                            f"--- Reference: {doc['original_name']} ---\n{content}"
                        )
                except Exception as read_err:
                    logger.warning(f"Could not read doc {doc['original_name']}: {read_err}")

            all_parts = []
            if doc_context_parts:
                all_parts.append(
                    f"=== REFERENCE DOCUMENTS ({len(doc_context_parts)} files) ===\n"
                    + "\n\n".join(doc_context_parts)
                )
            if repo_context_parts:
                all_parts.append(
                    f"=== REFERENCE REPOSITORIES ({len(repo_context_parts)} repos, packed by Repomix) ===\n"
                    "Use the code structure and patterns from these repos as reference for implementation.\n\n"
                    + "\n\n".join(repo_context_parts)
                )
            if all_parts:
                enriched_vision = f"{vision}\n\n" + "\n\n".join(all_parts)
                logger.info(
                    f"Enriched vision with {len(doc_context_parts)} docs, "
                    f"{len(repo_context_parts)} repos"
                )

        results = run_build_pipeline(
            job_id=job_id,
            workspace_path=job_workspace,
            vision=enriched_vision,
            config=job_config,
            progress_callback=progress_callback,
            job_db=job_db,
        )

        # Mark job as completed or failed based on task validation
        task_validation = results.get('task_validation', {})
        if task_validation.get('valid', True):  # If valid or no validation info
            job_db.mark_completed(job_id, {
                'status': results.get('status', 'completed'),
                'budget_report': results.get('budget_report', {}),
                'task_validation': task_validation
            })
        else:
            # Task validation failed - mark as failed
            incomplete_tasks = task_validation.get('incomplete_tasks', [])
            failed_tasks = task_validation.get('failed_tasks', [])
            error_msg = f"Task validation failed.\n"
            if incomplete_tasks:
                error_msg += f"\nIncomplete tasks ({len(incomplete_tasks)}): {', '.join(incomplete_tasks)}"
            if failed_tasks:
                error_msg += f"\nFailed tasks ({len(failed_tasks)}): {', '.join(failed_tasks)}"
            job_db.mark_failed(job_id, error_msg)
        
    except Exception as e:
        error_message = str(e)
        error_trace = traceback.format_exc()
        
        # Log to error file
        try:
            job = job_db.get_job(job_id)
            if job:
                error_log_path = Path(job['workspace_path']) / "crew_errors.log"
                with open(error_log_path, 'a') as f:
                    f.write(f"\n{'='*80}\n")
                    f.write(f"JOB FAILED - {datetime.now().isoformat()}\n")
                    f.write(f"{'='*80}\n")
                    f.write(f"Error Type: {type(e).__name__}\n")
                    f.write(f"Error Message: {error_message}\n")
                    f.write(f"Traceback:\n{error_trace}\n")
                    f.write(f"{'='*80}\n\n")
        except Exception as log_error:
            logger.error(f"Could not write to error log: {log_error}")
        
        # Check if it's quota exhaustion
        is_quota_exhausted = (
            'QUOTA_EXHAUSTED' in error_message or
            hasattr(e, 'quota_exhausted') and e.quota_exhausted or
            'exceeded your current quota' in error_message.lower() or
            '429' in error_message
        )
        
        if is_quota_exhausted:
            job_db.update_job(job_id, {
                'status': 'quota_exhausted',
                'error': (
                    "❌ API quota limit reached. "
                    "The job has been stopped. "
                    "Please check your API plan and billing details, or try again later. "
                    f"\n\nDetails: {error_message[:500]}"
                ),
                'completed_at': datetime.now().isoformat(),
                'current_phase': 'error'
            })
        else:
            job_db.mark_failed(job_id, f"{error_message}\n\nFull traceback available in crew_errors.log")


@app.route('/')
def index():
    """Main dashboard page"""
    try:
        return render_template('index.html')
    except Exception as e:
        return f"Error rendering template: {str(e)}", 500


@app.route('/health')
def health():
    """
    Basic health check endpoint
    Returns 200 if service is up
    """
    return jsonify({
        'status': 'healthy',
        'service': 'AI Software Development Crew',
        'version': '1.0.0',
        'timestamp': datetime.now().isoformat()
    }), 200


@app.route('/health/ready')
def health_ready():
    """
    Readiness check endpoint
    Verifies all critical dependencies are available
    Returns 200 if ready to serve requests, 503 if not ready
    """
    import logging
    import traceback
    
    logger = logging.getLogger(__name__)
    health_status = {
        'status': 'ready',
        'timestamp': datetime.now().isoformat(),
        'checks': {}
    }
    
    all_healthy = True
    
    # Check 1: Configuration
    try:
        if config is None:
            from ..config import ConfigLoader
            test_config = ConfigLoader.load()
        else:
            test_config = config
        
        health_status['checks']['config'] = {
            'status': 'healthy',
            'message': 'Configuration loaded successfully',
            'llm_environment': test_config.llm.environment
        }
    except Exception as e:
        all_healthy = False
        health_status['checks']['config'] = {
            'status': 'unhealthy',
            'message': f'Configuration error: {str(e)}'
        }
        logger.error(f"Health check - Config error: {e}")
    
    # Check 2: Workspace accessibility
    try:
        base_workspace_path.mkdir(parents=True, exist_ok=True)
        test_file = base_workspace_path / '.health_check'
        test_file.write_text('health check')
        test_file.unlink()
        
        health_status['checks']['workspace'] = {
            'status': 'healthy',
            'message': 'Workspace is writable',
            'path': str(base_workspace_path)
        }
    except Exception as e:
        all_healthy = False
        health_status['checks']['workspace'] = {
            'status': 'unhealthy',
            'message': f'Workspace error: {str(e)}'
        }
        logger.error(f"Health check - Workspace error: {e}")
    
    # Check 3: LLM connectivity (light check)
    try:
        from ..utils.llm_config import get_llm_for_agent
        
        # Only perform actual LLM check if config is healthy
        if health_status['checks']['config']['status'] == 'healthy':
            llm = get_llm_for_agent("worker", config)
            
            health_status['checks']['llm'] = {
                'status': 'healthy',
                'message': 'LLM initialized successfully',
                'provider': 'configured'
            }
        else:
            health_status['checks']['llm'] = {
                'status': 'skipped',
                'message': 'Skipped due to config error'
            }
    except Exception as e:
        all_healthy = False
        health_status['checks']['llm'] = {
            'status': 'unhealthy',
            'message': f'LLM initialization error: {str(e)}'
        }
        logger.error(f"Health check - LLM error: {e}")
    
    # Check 4: Job storage
    try:
        job_count = len(job_db.get_all_jobs())
        health_status['checks']['job_storage'] = {
            'status': 'healthy',
            'message': 'Job storage accessible',
            'active_jobs': job_count
        }
    except Exception as e:
        all_healthy = False
        health_status['checks']['job_storage'] = {
            'status': 'unhealthy',
            'message': f'Job storage error: {str(e)}'
        }
        logger.error(f"Health check - Job storage error: {e}")
    
    # Overall status
    if all_healthy:
        health_status['status'] = 'ready'
        return jsonify(health_status), 200
    else:
        health_status['status'] = 'not_ready'
        return jsonify(health_status), 503


@app.route('/health/live')
def health_live():
    """
    Liveness check endpoint
    Verifies the service is alive (for Kubernetes liveness probes)
    Returns 200 if process is running
    """
    return jsonify({
        'status': 'alive',
        'timestamp': datetime.now().isoformat()
    }), 200


@app.route('/health/llm')
def health_llm():
    """
    Deep LLM health check endpoint
    Actually tests LLM connectivity with a real API call
    Returns 200 if LLM is accessible, 503 if not
    """
    import logging
    import traceback
    
    logger = logging.getLogger(__name__)
    
    health_status = {
        'status': 'unknown',
        'timestamp': datetime.now().isoformat(),
        'checks': {}
    }
    
    try:
        # Load config
        if config is None:
            from ..config import ConfigLoader
            test_config = ConfigLoader.load()
        else:
            test_config = config
        
        health_status['checks']['config'] = {
            'status': 'healthy',
            'llm_environment': test_config.llm.environment,
            'api_base_url': test_config.llm.api_base_url or 'default'
        }
        
        # Test LLM connectivity
        from ..utils.llm_config import get_llm_for_agent
        
        llm = get_llm_for_agent("worker", test_config)
        
        # Perform a lightweight test completion
        test_prompt = "Say 'OK' if you can respond."
        
        import time
        start_time = time.time()
        response = llm.complete(test_prompt)
        response_time = time.time() - start_time
        
        health_status['checks']['llm_connectivity'] = {
            'status': 'healthy',
            'message': 'LLM responded successfully',
            'response_time_seconds': round(response_time, 3),
            'response_preview': str(response.text)[:100] if response.text else 'empty'
        }
        
        health_status['status'] = 'healthy'
        return jsonify(health_status), 200
        
    except Exception as e:
        error_trace = traceback.format_exc()
        health_status['status'] = 'unhealthy'
        health_status['checks']['llm_connectivity'] = {
            'status': 'unhealthy',
            'message': f'LLM connection failed: {str(e)}',
            'error_type': type(e).__name__
        }
        logger.error(f"Health check - LLM deep check failed: {e}")
        logger.debug(f"Traceback: {error_trace}")
        return jsonify(health_status), 503


import subprocess
import re
import shutil

# ── GitHub / Repomix integration ─────────────────────────────────────────────

GITHUB_URL_RE = re.compile(
    r'^https?://github\.com/[\w.\-]+/[\w.\-]+(/.*)?$'
)


def _is_github_url(url: str) -> bool:
    """Check if a string looks like a GitHub repository URL."""
    return bool(GITHUB_URL_RE.match(url.strip()))


def _run_repomix(github_url: str, job_workspace: Path, job_id: str) -> Optional[Dict[str, Any]]:
    """
    Use Repomix to pack a GitHub repo into an AI-friendly file.
    Returns dict with metadata or None on failure.
    Stores the packed output in workspace/docs/.
    """
    import logging
    logger = logging.getLogger(__name__)

    # Use absolute paths to avoid CWD issues
    abs_workspace = job_workspace.resolve()
    docs_dir = abs_workspace / 'docs'
    docs_dir.mkdir(parents=True, exist_ok=True)
    output_file = docs_dir / f"repomix-{job_id}.xml"

    # Normalise URL: strip trailing slash, /tree/branch etc
    clean_url = github_url.strip().rstrip('/')

    # Extract repo name early for logging
    parts = clean_url.rstrip('/').split('/')
    repo_name = '/'.join(parts[-2:]) if len(parts) >= 2 else parts[-1]

    logger.info(f"Starting Repomix for {repo_name}: {clean_url}")
    logger.info(f"Output path: {output_file}")

    try:
        result = subprocess.run(
            [
                'npx', '-y', 'repomix@latest',
                '--remote', clean_url,
                '--output', str(output_file),
                '--style', 'xml',
                '--compress',
            ],
            capture_output=True,
            text=True,
            timeout=600,  # 10 min max for large repos
        )

        logger.info(f"Repomix exit code: {result.returncode}")
        if result.stdout:
            logger.info(f"Repomix stdout: {result.stdout[:500]}")
        if result.stderr:
            logger.warning(f"Repomix stderr: {result.stderr[:500]}")

        if result.returncode != 0:
            logger.warning(f"Repomix failed (exit {result.returncode})")
            return None

        if not output_file.exists():
            logger.warning("Repomix completed but output file not found")
            return None

        file_size = output_file.stat().st_size
        if file_size < 100:
            logger.warning(f"Repomix output too small ({file_size} bytes), likely failed")
            return None

        logger.info(f"Repomix packed {repo_name} → {file_size} bytes")

        # Record as a document in DB
        doc_id = str(uuid.uuid4())
        doc = job_db.add_document(
            doc_id=doc_id,
            job_id=job_id,
            filename=output_file.name,
            original_name=f"github:{repo_name}",
            file_type='xml',
            file_size=file_size,
            stored_path=str(output_file),
        )

        return {
            'doc': doc,
            'repo': repo_name,
            'size': file_size,
        }

    except subprocess.TimeoutExpired:
        logger.error(f"Repomix timed out (600s) for {clean_url}")
        return None
    except FileNotFoundError:
        logger.error("npx not found – Node.js must be installed")
        return None
    except Exception as e:
        logger.error(f"Repomix error for {clean_url}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None


def _clone_github_repo(github_url: str, target_dir: Path, job_id: str) -> Optional[Dict[str, Any]]:
    """
    Clone a GitHub repository directly into the target directory.
    Used for migration mode where we need actual source files, not XML packs.
    
    If the clone creates a wrapper directory (common with GitHub), files are
    moved up to target_dir root and the wrapper is removed.
    
    Returns dict with metadata or None on failure.
    """
    import logging
    import shutil
    import tempfile
    logger = logging.getLogger(__name__)
    
    # Normalise URL
    clean_url = github_url.strip().rstrip('/')
    
    # Extract repo name for logging
    parts = clean_url.rstrip('/').split('/')
    repo_name = '/'.join(parts[-2:]) if len(parts) >= 2 else parts[-1]
    
    logger.info(f"Cloning GitHub repo for migration: {repo_name}")
    
    try:
        # Clone to a temp directory first to handle wrapper directories
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            clone_target = tmp_path / "clone"
            
            result = subprocess.run(
                ['git', 'clone', '--depth=1', clean_url, str(clone_target)],
                capture_output=True,
                text=True,
                timeout=300,  # 5 min max
            )
            
            if result.returncode != 0:
                logger.warning(f"Git clone failed (exit {result.returncode}): {result.stderr[:500]}")
                return None
            
            if not clone_target.exists():
                logger.warning("Git clone completed but directory not found")
                return None
            
            # Check if there's a single top-level directory (common with GitHub archives)
            contents = list(clone_target.iterdir())
            # Filter out .git
            non_git = [c for c in contents if c.name != '.git']
            
            source_dir = clone_target
            wrapper_dir = None
            
            # If single directory (ignoring .git), treat it as wrapper
            if len(non_git) == 1 and non_git[0].is_dir():
                wrapper_dir = non_git[0].name
                source_dir = non_git[0]
                logger.info(f"Detected wrapper directory: {wrapper_dir}")
            
            # Copy files from source_dir to target_dir
            file_count = 0
            for item in source_dir.rglob('*'):
                if item.is_file():
                    # Skip .git internals
                    if '.git' in item.parts:
                        continue
                    
                    rel_path = item.relative_to(source_dir)
                    dest = target_dir / rel_path
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, dest)
                    file_count += 1
            
            logger.info(f"Cloned {file_count} files from {repo_name}")
            
            return {
                'repo': repo_name,
                'files': file_count,
                'wrapper_dir': wrapper_dir,
            }
    
    except subprocess.TimeoutExpired:
        logger.error(f"Git clone timed out (300s) for {clean_url}")
        return None
    except FileNotFoundError:
        logger.error("git command not found – Git must be installed")
        return None
    except Exception as e:
        logger.error(f"Git clone error for {clean_url}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None


ALLOWED_EXTENSIONS = {
    'txt', 'md', 'pdf', 'json', 'yaml', 'yml', 'csv', 'xml',
    'py', 'js', 'ts', 'java', 'go', 'rs', 'rb', 'sh',
    'html', 'css', 'sql', 'proto', 'graphql',
    'png', 'jpg', 'jpeg', 'svg',
    'doc', 'docx', 'pptx', 'xlsx',
}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB per file
MAX_FILES_PER_JOB = 20


def _allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def _save_uploaded_files(job_id: str, job_workspace: Path, files) -> list:
    """Save uploaded files into workspace/docs/ and record in DB."""
    docs_dir = job_workspace / 'docs'
    docs_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for f in files:
        if not f or not f.filename:
            continue
        if not _allowed_file(f.filename):
            continue
        # Sanitise name: keep original for display, use uuid for storage
        import werkzeug.utils as wu
        safe_name = wu.secure_filename(f.filename)
        doc_id = str(uuid.uuid4())
        stored_name = f"{doc_id}_{safe_name}"
        stored_path = docs_dir / stored_name
        f.save(str(stored_path))
        file_size = stored_path.stat().st_size
        if file_size > MAX_FILE_SIZE:
            stored_path.unlink()
            continue
        ext = safe_name.rsplit('.', 1)[1].lower() if '.' in safe_name else 'unknown'
        doc = job_db.add_document(
            doc_id=doc_id,
            job_id=job_id,
            filename=stored_name,
            original_name=f.filename,
            file_type=ext,
            file_size=file_size,
            stored_path=str(stored_path),
        )
        saved.append(doc)
    return saved


def _extract_source_archive(job_workspace: Path, archive_file) -> int:
    """Extract a ZIP archive into the workspace root, preserving directory structure.

    If the ZIP has a single top-level directory (common when downloading a
    repo as ZIP), that wrapper directory is automatically stripped so that
    files land directly in the workspace root.

    Returns the number of files extracted.
    """
    data = archive_file.read()
    if not data:
        return 0

    try:
        zf_io = io.BytesIO(data)
        zf_test = zipfile.ZipFile(zf_io)
        zf_test.close()
    except zipfile.BadZipFile:
        print(f"[Migration] WARNING: Uploaded file is not a valid ZIP archive")
        return 0
    except Exception as e:
        print(f"[Migration] WARNING: Failed to read archive: {e}")
        return 0

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        # Detect single top-level folder to strip
        names = [n for n in zf.namelist() if not n.endswith('/')]
        top_dirs: set[str] = set()
        for n in names:
            first_segment = n.split('/')[0]
            top_dirs.add(first_segment)

        strip_prefix = ''
        if len(top_dirs) == 1:
            only_dir = top_dirs.pop()
            # Only strip if it really is a directory wrapper (not a single file)
            if any('/' in n for n in names):
                strip_prefix = only_dir + '/'

        extracted = 0
        for info in zf.infolist():
            if info.is_dir():
                continue

            rel_path = info.filename
            if strip_prefix and rel_path.startswith(strip_prefix):
                rel_path = rel_path[len(strip_prefix):]
            if not rel_path:
                continue

            # Security: reject path traversal
            parts = rel_path.replace('\\', '/').split('/')
            if any(p == '..' for p in parts):
                continue
            clean = '/'.join(p for p in parts if p and p != '.')
            if not clean:
                continue

            target = job_workspace / clean
            target.parent.mkdir(parents=True, exist_ok=True)

            with zf.open(info) as src:
                target.write_bytes(src.read())

            # Enforce per-file size limit
            if target.stat().st_size > MAX_FILE_SIZE:
                target.unlink()
                continue

            extracted += 1

    return extracted


@app.route('/api/backends', methods=['GET'])
def list_backends():
    """List available agentic backends."""
    try:
        from src.llamaindex_crew.backends import registry
        backends = registry.list_backends()
        return jsonify({'backends': backends}), 200
    except Exception as e:
        print(f"Error listing backends: {e}")
        # Fallback to OPL only if import fails
        return jsonify({'backends': [
            {'name': 'opl-ai-team', 'display_name': 'OPL AI Team', 'available': True}
        ]}), 200


@app.route('/api/jobs', methods=['POST'])
def create_job():
    """Create a new build job. Accepts JSON or multipart/form-data with files.

    For migration projects, pass ``mode=migration`` to skip the build pipeline
    and send source files via the ``source_files``/``source_paths`` fields.
    """
    # Support both JSON and multipart
    github_urls = []
    backend_name = 'opl-ai-team'  # default
    mode = 'build'
    
    if request.content_type and 'multipart/form-data' in request.content_type:
        vision = request.form.get('vision', '')
        backend_name = request.form.get('backend', 'opl-ai-team')
        # GitHub URLs can come as repeated form fields
        github_urls = request.form.getlist('github_urls')
        mode = request.form.get('mode', 'build')
    else:
        data = request.json or {}
        vision = data.get('vision', '')
        backend_name = data.get('backend', 'opl-ai-team')
        github_urls = data.get('github_urls', [])
        mode = data.get('mode', 'build')
    
    # Validate backend
    try:
        from src.llamaindex_crew.backends import registry
        backend = registry.get_backend(backend_name)
        if not backend:
            return jsonify({'error': f'Unknown backend: {backend_name}'}), 400
        if not backend.is_available():
            return jsonify({'error': f'Backend not available: {backend_name}'}), 400
    except Exception as e:
        print(f"Error loading backend registry: {e}")
        # Fallback: only allow opl-ai-team
        if backend_name != 'opl-ai-team':
            return jsonify({'error': f'Backend not available: {backend_name}'}), 400
        backend = None  # Will use old run_job_async path
    
    job_id = str(uuid.uuid4())
    
    if not vision:
        return jsonify({'error': 'Vision is required'}), 400
    
    # Create job-specific workspace folder
    job_workspace = base_workspace_path / f"job-{job_id}"
    job_workspace.mkdir(parents=True, exist_ok=True)
    
    # Create job record in database
    job_db.create_job(job_id, vision, str(job_workspace))
    
    # Save any uploaded documents (MTA reports end up here)
    uploaded_docs = []
    if request.files:
        files = request.files.getlist('documents')
        if len(files) > MAX_FILES_PER_JOB:
            files = files[:MAX_FILES_PER_JOB]
        uploaded_docs = _save_uploaded_files(job_id, job_workspace, files)

    # ── Migration/Refactor mode: extract source ZIP to workspace root, skip build ──
    source_count = 0
    if mode in ('migration', 'refactor'):
        print(f"[Migration] mode=migration, request.files keys: {list(request.files.keys()) if request.files else 'NONE'}")
        if request.files:
            archive = request.files.get('source_archive')
            print(f"[Migration] source_archive present: {archive is not None}, filename: {archive.filename if archive else 'N/A'}")
            if archive and archive.filename:
                source_count = _extract_source_archive(job_workspace, archive)
                print(f"[Migration] Extracted {source_count} files from ZIP")
            else:
                print("[Migration] WARNING: No source_archive file in request. Source code will be missing!")
        else:
            print("[Migration] WARNING: request.files is empty!")

    valid_urls = [u for u in github_urls if u and _is_github_url(u)]

    if mode in ('migration', 'refactor'):
        # Clone GitHub repos directly to workspace root (not packed as XML)
        github_file_count = 0
        for url in valid_urls:
            try:
                result = _clone_github_repo(url, job_workspace, job_id)
                if result:
                    github_file_count += result.get('files', 0)
            except Exception as e:
                print(f"Failed to clone {url}: {e}")
                # Continue with other repos even if one fails
        
        # For migration/refactor projects we only persist files — the build pipeline
        # is NOT started. The frontend calls POST /api/jobs/<id>/{migrate|refactor}
        # separately to kick off the runner.
        phase = 'awaiting_migration' if mode == 'migration' else 'awaiting_refactor'
        job_db.update_job(job_id, {'status': 'queued', 'current_phase': phase})
        return jsonify({
            'job_id': job_id,
            'status': 'queued',
            'documents': len(uploaded_docs),
            'source_files': source_count + github_file_count,
            'github_repos': len(valid_urls),
        }), 201

    # Process GitHub URLs with Repomix (in background thread to not block response)
    repomix_count = 0

    def process_github_and_run():
        """Process GitHub repos first, then run the job."""
        nonlocal repomix_count
        import logging
        logger = logging.getLogger(__name__)
        
        # Mark job as started so status shows "running"
        job_db.update_job(job_id, {'status': 'running'})
        
        # Pack each GitHub repo with Repomix
        for i, url in enumerate(valid_urls):
            progress = 2 + (i * 3)
            logger.info(f"Processing GitHub URL ({i+1}/{len(valid_urls)}): {url}")
            job_db.update_progress(job_id, 'fetching_context',
                                   progress, f"Packing reference repo with Repomix: {url}")
            result = _run_repomix(url, job_workspace, job_id)
            if result:
                repomix_count += 1
                logger.info(f"Packed {result['repo']} ({result['size']} bytes)")
            else:
                logger.warning(f"Failed to pack {url} – continuing without it")
        
        if repomix_count > 0:
            logger.info(f"Successfully packed {repomix_count}/{len(valid_urls)} repos")
        
        # Reset status to queued so run_job_async can mark_started properly
        job_db.update_job(job_id, {'status': 'queued'})
        
        # Now run the actual job with the selected backend
        if backend and backend.name != 'opl-ai-team':
            # Use pluggable backend (e.g., Aider)
            run_job_with_backend(job_id, vision, backend)
        else:
            # Use original OPL path (preserves all existing functionality)
            run_job_async(job_id, vision, config)

    if valid_urls:
        # Start combined GitHub-fetch + job thread
        thread = threading.Thread(target=process_github_and_run)
    else:
        # Start job directly (no GitHub repos to fetch)
        if backend and backend.name != 'opl-ai-team':
            # Use pluggable backend (e.g., Aider)
            thread = threading.Thread(
                target=run_job_with_backend,
                args=(job_id, vision, backend)
            )
        else:
            # Use original OPL path (preserves all existing functionality)
            thread = threading.Thread(
                target=run_job_async,
                args=(job_id, vision, config)
            )
    thread.daemon = True
    thread.start()
    
    return jsonify({
        'job_id': job_id,
        'status': 'queued',
        'documents': len(uploaded_docs),
        'github_repos': len(valid_urls),
    }), 201


@app.route('/api/jobs/<job_id>/documents', methods=['GET'])
def get_job_documents(job_id):
    """List all reference documents attached to a job."""
    job = job_db.get_job(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    docs = job_db.get_job_documents(job_id)
    return jsonify({'documents': docs})


@app.route('/api/jobs/<job_id>/documents', methods=['POST'])
def upload_job_documents(job_id):
    """Upload additional documents to an existing job."""
    job = job_db.get_job(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    
    if not request.files:
        return jsonify({'error': 'No files provided'}), 400
    
    files = request.files.getlist('documents')
    existing = len(job_db.get_job_documents(job_id))
    remaining = MAX_FILES_PER_JOB - existing
    if remaining <= 0:
        return jsonify({'error': f'Maximum {MAX_FILES_PER_JOB} documents per job'}), 400
    
    files = files[:remaining]
    job_workspace = Path(job['workspace_path'])
    saved = _save_uploaded_files(job_id, job_workspace, files)
    return jsonify({'uploaded': len(saved), 'documents': saved}), 201


@app.route('/api/jobs/<job_id>/documents/<doc_id>', methods=['DELETE'])
def delete_job_document(job_id, doc_id):
    """Delete a reference document from a job."""
    job = job_db.get_job(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    
    docs = job_db.get_job_documents(job_id)
    doc = next((d for d in docs if d['id'] == doc_id), None)
    if not doc:
        return jsonify({'error': 'Document not found'}), 404
    
    # Remove file from disk
    try:
        Path(doc['stored_path']).unlink(missing_ok=True)
    except Exception:
        pass
    
    job_db.delete_document(doc_id)
    return jsonify({'status': 'deleted'})


@app.route('/api/jobs', methods=['GET'])
def list_jobs():
    """List all jobs"""
    return jsonify({
        'jobs': [
            {
                'id': job['id'],
                'vision': job['vision'][:100] + '...' if len(job['vision']) > 100 else job['vision'],
                'status': job['status'],
                'progress': job['progress'],
                'current_phase': job['current_phase'],
                'created_at': job['created_at'],
                'completed_at': job.get('completed_at')
            }
            for job in job_db.get_all_jobs()
        ]
    })


@app.route('/api/jobs/<job_id>', methods=['GET'])
def get_job(job_id):
    """Get job status and details"""
    job = job_db.get_job(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    
    return jsonify(job)


@app.route('/api/jobs/<job_id>/progress', methods=['GET'])
def get_job_progress(job_id):
    """Get job progress"""
    job = job_db.get_job(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    
    return jsonify({
        'status': job['status'],
        'progress': job['progress'],
        'current_phase': job['current_phase'],
        'last_message': job.get('last_message', [])[-10:]  # Last 10 messages
    })


@app.route('/api/jobs/<job_id>/files', methods=['GET'])
def list_job_files(job_id):
    """List files generated by job"""
    job = job_db.get_job(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    
    workspace_path = Path(job['workspace_path'])
    
    if not workspace_path.exists():
        return jsonify({'files': []})
    
    files = []
    for file_path in workspace_path.rglob('*'):
        if file_path.is_file():
            rel_path = file_path.relative_to(workspace_path)
            files.append({
                'path': str(rel_path),
                'size': file_path.stat().st_size,
                'modified': datetime.fromtimestamp(file_path.stat().st_mtime).isoformat()
            })
    
    return jsonify({'files': files})


@app.route('/api/jobs/<job_id>/tasks', methods=['GET'])
def get_job_tasks(job_id):
    """Return one task entry per agent/phase with progress info."""
    job = job_db.get_job(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404

    workspace_path = Path(job['workspace_path'])
    db_path = workspace_path / f"tasks_{job_id}.db"

    # ── Phase metadata (matches AGENT_DEFINITIONS & workflow state machine) ──
    PHASE_META = {
        'meta':           {'agent': 'Meta Agent',    'description': 'Planning project approach and task breakdown'},
        'product_owner':  {'agent': 'Product Owner', 'description': 'Defining user stories and acceptance criteria'},
        'designer':       {'agent': 'Designer',      'description': 'Creating wireframes and design specifications'},
        'tech_architect': {'agent': 'Tech Architect','description': 'System design and technology decisions'},
        'development':    {'agent': 'Dev Crew',      'description': 'Implementing core application logic'},
        'frontend':       {'agent': 'Frontend Crew', 'description': 'Building the user interface'},
    }

    # ── Determine phase status from current_phase ──
    current_phase = job.get('current_phase', 'queued')
    job_status = job.get('status', 'queued')

    if job_status == 'completed' or current_phase == 'completed':
        current_idx = len(PHASE_ORDER)
    elif current_phase in PHASE_ORDER:
        current_idx = PHASE_ORDER.index(current_phase)
    else:
        current_idx = -1

    # ── Try to read real subtask counts from SQLite ──
    phase_counts = {}  # {phase: {total, completed, in_progress}}
    if not db_path.exists():
        db_files = list(workspace_path.glob('tasks_*.db'))
        if db_files:
            db_path = db_files[0]

    if db_path.exists():
        try:
            from src.llamaindex_crew.orchestrator.task_manager import TaskManager
            task_manager = TaskManager(db_path, job_id)
            for task in task_manager.get_all_tasks():
                phase = task.phase
                if phase not in phase_counts:
                    phase_counts[phase] = {'total': 0, 'completed': 0, 'in_progress': 0}
                phase_counts[phase]['total'] += 1
                st = task_manager.get_task_status(task.task_id)
                if st and st.value == 'completed':
                    phase_counts[phase]['completed'] += 1
                elif st and st.value == 'in_progress':
                    phase_counts[phase]['in_progress'] += 1
        except Exception as e:
            print(f"Warning: could not read task DB: {e}")

    # ── Build one task per phase ──
    tasks = []
    for i, phase in enumerate(PHASE_ORDER):
        meta = PHASE_META.get(phase, {'agent': phase, 'description': phase})

        if current_idx < 0:
            status = 'pending'
        elif i < current_idx:
            status = 'completed'
        elif i == current_idx:
            status = 'in_progress'
        else:
            status = 'pending'

        counts = phase_counts.get(phase, {'total': 0, 'completed': 0, 'in_progress': 0})
        total = counts['total']
        completed = counts['completed']
        progress = int((completed / total) * 100) if total > 0 else (100 if status == 'completed' else 0)

        tasks.append({
            'task_id': f'phase-{phase}',
            'phase': phase,
            'task_type': phase.replace('_', ' ').title(),
            'agent': meta['agent'],
            'description': meta['description'],
            'status': status,
            'subtasks_total': total,
            'subtasks_completed': completed,
            'subtasks_in_progress': counts['in_progress'],
            'progress': progress,
        })

    return jsonify({
        'total_tasks': len(tasks),
        'tasks': tasks,
    })


# ── Agent Definitions ──────────────────────────────────────────────────────
# Build workflow: ordered by phase sequence
AGENT_DEFINITIONS = [
    {'name': 'Meta Agent',    'role': 'Orchestrator',      'model': 'deepseek-r1-distill-qwen-14b', 'phase': 'meta'},
    {'name': 'Product Owner', 'role': 'Requirements',      'model': 'qwen3-14b',                    'phase': 'product_owner'},
    {'name': 'Designer',      'role': 'UX/UI',             'model': 'granite-3-2-8b-instruct',      'phase': 'designer'},
    {'name': 'Tech Architect','role': 'System Design',     'model': 'qwen3-14b',                    'phase': 'tech_architect'},
    {'name': 'Dev Crew',      'role': 'Implementation',    'model': 'qwen3-14b',                    'phase': 'development'},
    {'name': 'Frontend Crew', 'role': 'UI Implementation', 'model': 'granite-3-2-8b-instruct',      'phase': 'frontend'},
]

PHASE_ORDER = [a['phase'] for a in AGENT_DEFINITIONS]

# Refactor workflow: architect (analyze → design → plan) → executor → devops
REFACTOR_AGENT_DEFINITIONS = [
    {'name': 'Refactor Architect (Analysis)', 'role': 'Source Analysis',     'model': 'qwen3-14b', 'phase': 'analysis'},
    {'name': 'Refactor Architect (Design)',  'role': 'Target Architecture',  'model': 'qwen3-14b', 'phase': 'design'},
    {'name': 'Refactor Architect (Plan)',    'role': 'Refactor Plan',        'model': 'qwen3-14b', 'phase': 'planning'},
    {'name': 'Refactor Executor',            'role': 'Code Migration',        'model': 'qwen3-14b', 'phase': 'execution'},
    {'name': 'DevOps',                       'role': 'Container & Pipeline', 'model': 'qwen3-14b', 'phase': 'devops'},
]

REFACTOR_PHASE_ORDER = [a['phase'] for a in REFACTOR_AGENT_DEFINITIONS]

# Phases that indicate job is in refactor flow (roster shows refactor + devops agents)
REFACTOR_PHASES = {'refactoring', 'analysis', 'design', 'planning', 'execution', 'devops', 'refactor_failed'}


@app.route('/api/jobs/<job_id>/agents', methods=['GET'])
def get_job_agents(job_id):
    """Get agent statuses derived from job's current phase.
    Build jobs: meta → product_owner → designer → tech_architect → development → frontend.
    Refactor jobs: analysis → design → planning → execution → devops (shows Refactor Architect, Executor, DevOps).
    """
    job = job_db.get_job(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404

    current_phase = job.get('current_phase', 'queued')
    job_status = job.get('status', 'queued')
    messages = job.get('last_message', [])

    # Use refactor roster when job is in refactor flow
    if current_phase in REFACTOR_PHASES:
        definitions = REFACTOR_AGENT_DEFINITIONS
        phase_order = REFACTOR_PHASE_ORDER
    else:
        definitions = AGENT_DEFINITIONS
        phase_order = PHASE_ORDER

    if job_status == 'completed' or current_phase == 'completed':
        current_idx = len(phase_order)
    elif current_phase in phase_order:
        current_idx = phase_order.index(current_phase)
    elif current_phase == 'refactoring' and phase_order == REFACTOR_PHASE_ORDER:
        current_idx = 0  # Refactor just started → first agent (Analysis) working
    else:
        current_idx = -1

    agents = []
    for i, defn in enumerate(definitions):
        if current_idx < 0:
            status = 'idle'
        elif i < current_idx:
            status = 'completed'
        elif i == current_idx:
            status = 'working'
        else:
            status = 'idle'

        phase_messages = [m for m in messages if m.get('phase') == defn['phase']]
        last_msg = phase_messages[-1] if phase_messages else None

        agents.append({
            'name': defn['name'],
            'role': defn['role'],
            'model': defn['model'],
            'status': status,
            'phase': defn['phase'],
            'last_activity': last_msg['message'] if last_msg else None,
            'last_activity_at': last_msg['timestamp'] if last_msg else None,
        })

    return jsonify({'agents': agents})


@app.route('/api/jobs/<job_id>/budget', methods=['GET'])
def get_job_budget(job_id):
    """Get budget report for job"""
    job = job_db.get_job(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    
    try:
        from src.llamaindex_crew.budget.tracker import EnhancedBudgetTracker
        tracker = EnhancedBudgetTracker()
        report = tracker.get_report(job_id)
        return jsonify(report)
    except Exception as e:
        return jsonify({'error': f'Could not get budget: {str(e)}'}), 500


@app.route('/api/stats', methods=['GET'])
def get_stats():
    """Get system statistics"""
    return jsonify(job_db.get_stats())


@app.route('/api/workspace/files', methods=['GET'])
def list_workspace_files():
    """List files in workspace (all jobs or specific job)"""
    try:
        job_id = request.args.get('job_id')
        
        if job_id:
            job = job_db.get_job(job_id)
            if job:
                # List files for specific job (resolve in case stored path is relative)
                job_workspace = _resolve_job_workspace(job_id, job['workspace_path']) or Path(job['workspace_path'])
                
                # For refactor jobs, scope to 'refactored' subdirectory if it exists
                # This ensures the UI only shows the target code, not legacy source.
                if job.get('vision', '').startswith('[Refactor]') or job.get('current_phase') == 'refactoring':
                    refactored_dir = job_workspace / "refactored"
                    if refactored_dir.is_dir():
                        job_workspace = refactored_dir
            else:
                # Job not found, default to base workspace
                job_workspace = base_workspace_path
        else:
            # List files from all jobs
            job_workspace = base_workspace_path
        
        files = []
        if job_workspace and job_workspace.exists():
            for root, dirs, filenames in os.walk(job_workspace):
                for filename in filenames:
                    file_path = Path(root) / filename
                    rel_path = file_path.relative_to(job_workspace)
                    files.append({
                        'path': str(rel_path),
                        'size': file_path.stat().st_size,
                        'modified': datetime.fromtimestamp(file_path.stat().st_mtime).isoformat()
                    })
        return jsonify({'files': files, 'workspace': str(job_workspace)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def _resolve_job_workspace(job_id: str, stored_path: str) -> Optional[Path]:
    """Resolve job workspace path. Tries stored path, then base_workspace_path/job-{id}."""
    p = Path(stored_path)
    if p.is_absolute() and p.exists() and p.is_dir():
        return p
    if p.exists() and p.is_dir():
        return p
    # Stored path may be relative to a different cwd; use canonical location
    canonical = base_workspace_path / f"job-{job_id}"
    if canonical.exists() and canonical.is_dir():
        return canonical
    return None


# Files/dirs to exclude from project download (internal agent/crew artifacts)
_DOWNLOAD_EXCLUDE_NAMES = frozenset({
    'agent_prompts.json',
    'agents_prompt.json',
    'crew_errors.log',
})
_DOWNLOAD_EXCLUDE_PATTERNS = ('state_*.json', 'tasks_*.db')


def _should_exclude_from_download(rel_path_str: str, name: str) -> bool:
    """Return True if this path should be omitted from the download ZIP."""
    if name in _DOWNLOAD_EXCLUDE_NAMES:
        return True
    for pattern in _DOWNLOAD_EXCLUDE_PATTERNS:
        if fnmatch.fnmatch(name, pattern):
            return True
    # Skip .git directory and its contents
    parts = rel_path_str.replace('\\', '/').split('/')
    if '.git' in parts:
        return True
    return False


@app.route('/api/jobs/<job_id>/download', methods=['GET'])
def download_job_workspace(job_id):
    """Return the job workspace as a ZIP file for download (excludes internal agent files)."""
    job = job_db.get_job(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    workspace_path = _resolve_job_workspace(job_id, job['workspace_path'])
    if not workspace_path:
        return jsonify({'error': 'Workspace not found'}), 404
    
    # For refactor jobs, only download the refactored results
    if job.get('vision', '').startswith('[Refactor]') or job.get('current_phase') == 'refactoring':
        refactored_dir = workspace_path / "refactored"
        if refactored_dir.is_dir():
            workspace_path = refactored_dir
    buf = io.BytesIO()
    workspace_resolved = workspace_path.resolve()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, filenames in os.walk(workspace_path):
            dirs[:] = [d for d in dirs if d != '.git']
            for name in filenames:
                full = Path(root) / name
                try:
                    rel = full.resolve().relative_to(workspace_resolved)
                except ValueError:
                    continue
                arcname = str(rel).replace('\\', '/')
                if _should_exclude_from_download(arcname, name):
                    continue
                zf.write(full, arcname)
    buf.seek(0)
    safe_name = f"project-{job_id[:8]}.zip"
    try:
        return send_file(
            buf,
            mimetype='application/zip',
            as_attachment=True,
            download_name=safe_name,
        )
    except TypeError:
        # Flask < 2.0 used attachment_filename
        return send_file(
            buf,
            mimetype='application/zip',
            as_attachment=True,
            attachment_filename=safe_name,
        )


@app.route('/api/workspace/files/<path:file_path>', methods=['GET'])
def get_file_content(file_path):
    """Get file content from workspace (supports job-specific paths)"""
    try:
        job_id = request.args.get('job_id')
        
        if job_id:
            job = job_db.get_job(job_id)
            if job:
                # Get file from specific job workspace
                job_workspace = Path(job['workspace_path'])
                
                # For refactor jobs, look in the 'refactored' subdirectory first
                if job.get('vision', '').startswith('[Refactor]') or job.get('current_phase') == 'refactoring':
                    refactored_dir = job_workspace / "refactored"
                    if refactored_dir.is_dir():
                        job_workspace = refactored_dir
                        
                full_path = job_workspace / file_path
            else:
                # Job not found, fallback to base workspace
                full_path = base_workspace_path / file_path
        else:
            # Try to find file in any job workspace
            full_path = None
            for job in job_db.get_all_jobs():
                job_workspace = Path(job['workspace_path'])
                potential_path = job_workspace / file_path
                if potential_path.exists() and potential_path.is_file():
                    full_path = potential_path
                    break
            
            if full_path is None:
                # Fallback to base workspace
                full_path = base_workspace_path / file_path
        
        if not full_path.exists() or not full_path.is_file():
            return jsonify({'error': 'File not found'}), 404
        
        with open(full_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        return jsonify({
            'path': file_path,
            'content': content
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def _is_safe_relative_path(path: str) -> bool:
    """Reject path escape: no '..', no absolute path, no null bytes."""
    if not path or path.startswith('/') or '..' in path or '\x00' in path:
        return False
    # Only allow simple relative paths (letters, digits, slashes, dots, hyphens, underscores)
    return all(c.isalnum() or c in '/._ -' for c in path)


@app.route('/api/jobs/<job_id>/refine', methods=['POST'])
def refine_job(job_id):
    """Start a refinement run for a completed/failed job. Returns 202 or 409 if already refining."""
    job = job_db.get_job(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    if job['status'] == 'running':
        return jsonify({'error': 'Job is still running'}), 400
    if job.get('current_phase') == 'refining':
        return jsonify({'error': 'Refinement already in progress'}), 409
    data = request.get_json() or {}
    prompt = (data.get('prompt') or '').strip()
    if not prompt:
        return jsonify({'error': 'prompt is required'}), 400
    file_path = data.get('file_path')
    if file_path is not None:
        file_path = file_path.strip() if isinstance(file_path, str) else None
        if file_path and not _is_safe_relative_path(file_path):
            return jsonify({'error': 'Invalid file_path'}), 400
    refinement_id = str(uuid.uuid4())
    job_db.create_refinement(refinement_id, job_id, prompt, file_path)
    # Mark job as running so dashboard shows it as running and tracks refinement progress
    previous_status = job.get('status') or 'completed'
    job_db.update_job(job_id, {'status': 'running'})
    job_db.update_progress(job_id, 'refining', 0, 'Refinement started.')
    def progress_cb(phase: str, progress: int, message: Optional[str] = None):
        job_db.update_progress(job_id, phase, progress, message or '')

    def run():
        from crew_studio.refinement_runner import run_refinement
        run_refinement(
            job_id=job_id,
            workspace_path=Path(job['workspace_path']),
            prompt=prompt,
            refinement_id=refinement_id,
            job_db=job_db,
            progress_callback=progress_cb,
            file_path=file_path,
            previous_status=previous_status,
        )
    thread = threading.Thread(target=run)
    thread.daemon = True
    thread.start()
    return jsonify({'status': 'refining', 'message': 'Refinement started', 'refinement_id': refinement_id}), 202


@app.route('/api/jobs/<job_id>/refinements', methods=['GET'])
def get_job_refinements(job_id):
    """List refinement history for a job."""
    job = job_db.get_job(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    refinements = job_db.get_refinement_history(job_id)
    return jsonify({'refinements': refinements})


@app.route('/api/jobs/<job_id>/preview/<path:file_path>', methods=['GET'])
def serve_job_preview(job_id, file_path):
    """Serve a file from the job workspace for HTML preview (correct Content-Type)."""
    # URL-decode so paths like src%2Findex.html become src/index.html
    file_path = unquote(file_path)
    job = job_db.get_job(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    if not _is_safe_relative_path(file_path):
        return jsonify({'error': 'Invalid path'}), 400
    workspace = Path(job['workspace_path'])
    if not workspace.exists():
        return jsonify({'error': 'Workspace not found'}), 404
    full_path = (workspace / file_path).resolve()
    workspace_resolved = workspace.resolve()
    try:
        full_path.relative_to(workspace_resolved)
    except ValueError:
        return jsonify({'error': 'Invalid path'}), 400
    if not full_path.exists() or not full_path.is_file():
        return jsonify({'error': 'File not found'}), 404
    ext = full_path.suffix.lower()
    content_types = {
        '.html': 'text/html', '.htm': 'text/html',
        '.js': 'application/javascript', '.mjs': 'application/javascript',
        '.css': 'text/css', '.json': 'application/json',
        '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
        '.gif': 'image/gif', '.svg': 'image/svg+xml', '.ico': 'image/x-icon',
        '.woff': 'font/woff', '.woff2': 'font/woff2',
    }
    mimetype = content_types.get(ext, 'application/octet-stream')
    return send_from_directory(str(workspace), file_path, mimetype=mimetype)


# ── Phase sets for job-type classification (restart) ─────────────────────
_MIGRATION_JOB_PHASES = frozenset({
    'migrating', 'migration_failed', 'awaiting_migration',
})
_REFACTOR_JOB_PHASES = frozenset({
    'refactoring', 'analysis', 'design', 'planning', 'execution',
    'devops', 'refactor_failed', 'awaiting_refactor',
})
_RESTARTABLE_STATUSES = frozenset({'failed', 'cancelled', 'quota_exhausted', 'completed'})


@app.route('/api/jobs/<job_id>/restart', methods=['POST'])
def restart_job(job_id):
    """Restart a failed / cancelled / quota-exhausted job.

    Classifies the job as build, migration, or refactor and takes the
    appropriate action to start it again.
    """
    job = job_db.get_job(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404

    if job['status'] not in _RESTARTABLE_STATUSES:
        return jsonify({
            'error': f"Job is not restartable (status={job['status']}). "
                     "Only completed, failed, cancelled, or quota_exhausted jobs can be restarted."
        }), 400

    phase = job.get('current_phase', '')
    vision = job.get('vision', '')

    # ── Migration job ─────────────────────────────────────────────────────
    if phase in _MIGRATION_JOB_PHASES or vision.startswith('[MTA]'):
        job_db.fail_stale_migrations(job_id)

        # Determine whether we can do a targeted retry (some issues exist)
        # or need a full re-run (no issues recorded yet).
        failed_issues = job_db.get_failed_migration_issues(job_id)
        all_issues = job_db.get_migration_issues(job_id)
        has_issues = len(all_issues) > 0
        has_failures = len(failed_issues) > 0

        job_db.update_job(job_id, {
            'status': 'running',
            'current_phase': 'migrating',
            'error': None,
        })

        def _mig_progress(p_phase, pct, msg):
            job_db.update_progress(job_id, p_phase, pct, msg)

        def _run_migration_thread():
            try:
                ws_path = job.get('workspace_path', '')
                ws = Path(ws_path)
                if not ws.is_dir():
                    ws = base_workspace_path / f"job-{job_id}"

                if has_issues and has_failures:
                    # Retry only the failed tasks
                    from crew_studio.migration.runner import run_migration_retry
                    run_migration_retry(
                        job_id=job_id,
                        workspace_path=str(ws),
                        migration_goal='Analyse the MTA report and apply all migration changes',
                        job_db=job_db,
                        progress_callback=_mig_progress,
                    )
                else:
                    # Full re-run — delete old issues first to avoid duplicates
                    deleted = job_db.delete_migration_issues(job_id)
                    if deleted:
                        logger.info("Cleaned up %d old migration issues before re-run for job %s", deleted, job_id)

                    from crew_studio.migration.runner import run_migration
                    docs = job_db.get_job_documents(job_id)
                    report_doc = None
                    for doc in docs:
                        name_lower = doc['original_name'].lower()
                        if any(kw in name_lower for kw in ('mta', 'migration', 'report', 'analysis', 'issues')):
                            report_doc = doc
                            break
                    if not report_doc and docs:
                        report_doc = docs[0]
                    report_rel = report_doc['stored_path'] if report_doc else ''
                    try:
                        report_rel = str(Path(report_doc['stored_path']).relative_to(ws))
                    except (ValueError, AttributeError):
                        pass
                    run_migration(
                        job_id=job_id,
                        workspace_path=str(ws),
                        migration_goal='Analyse the MTA report and apply all migration changes',
                        report_path=report_rel,
                        migration_notes=None,
                        job_db=job_db,
                        progress_callback=_mig_progress,
                    )

                # Check for remaining failures/pending before marking completed
                remaining_failed = job_db.get_failed_migration_issues(job_id)
                summary = job_db.get_migration_summary(job_id)
                if remaining_failed:
                    n = len(remaining_failed)
                    sample = remaining_failed[0].get('error') or 'Unknown'
                    job_db.update_job(job_id, {
                        'status': 'failed',
                        'current_phase': 'migration_failed',
                        'error': f"{n} migration task(s) failed. Example: {sample[:400]}",
                    })
                elif summary.get('pending', 0) > 0:
                    job_db.update_job(job_id, {
                        'status': 'failed',
                        'current_phase': 'migration_failed',
                        'error': f"{summary['pending']} task(s) still pending — migration did not complete fully",
                    })
                else:
                    job_db.update_job(job_id, {'status': 'completed', 'current_phase': 'completed'})
            except Exception as e:
                job_db.update_job(job_id, {
                    'status': 'failed',
                    'current_phase': 'migration_failed',
                    'error': str(e)[:1000],
                })

        thread = threading.Thread(target=_run_migration_thread, daemon=True)
        thread.start()
        retry_mode = "retry_failed" if (has_issues and has_failures) else "full"
        return jsonify({
            'status': 'restarted',
            'job_type': 'migration',
            'job_id': job_id,
            'mode': retry_mode,
            'failed_issues': len(failed_issues),
        }), 202

    # ── Refactor job ──────────────────────────────────────────────────────
    if phase in _REFACTOR_JOB_PHASES:
        job_db.update_job(job_id, {
            'status': 'queued',
            'current_phase': 'awaiting_refactor',
            'error': None,
        })
        return jsonify({'status': 'restarted', 'job_type': 'refactor', 'job_id': job_id}), 202

    # ── Build job (default) ───────────────────────────────────────────────
    job_db.update_job(job_id, {
        'status': 'queued',
        'current_phase': 'starting',
        'progress': 0,
        'error': None,
    })
    thread = threading.Thread(
        target=run_job_async,
        args=(job_id, vision, config),
        daemon=True,
    )
    thread.start()
    return jsonify({'status': 'restarted', 'job_type': 'build', 'job_id': job_id}), 202


@app.route('/api/jobs/<job_id>/cancel', methods=['POST'])
def cancel_job(job_id):
    """Cancel a running job"""
    job = job_db.get_job(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    
    job = job_db.get_job(job_id)
    if job and job['status'] in ['completed', 'failed', 'cancelled']:
        return jsonify({'error': 'Job is not running'}), 400
    
    job_db.mark_cancelled(job_id)
    
    return jsonify({'status': 'cancelled'})


if __name__ == '__main__':
    # Resume / clean-up any jobs that were in-flight when the server last stopped
    resume_pending_jobs()

    # Default 8081 to avoid conflict with JBoss/EAP on 8080
    port = int(os.getenv('PORT', 8081))
    # use_reloader=False is critical: the Werkzeug reloader holds an import
    # lock that deadlocks background threads doing lazy imports (e.g. the
    # SoftwareDevWorkflow import inside run_job_async).  It also kills
    # in-flight job threads on every file change.
    app.run(host='0.0.0.0', port=port, debug=True, use_reloader=False)
