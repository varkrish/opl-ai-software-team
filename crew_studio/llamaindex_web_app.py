"""
Web GUI Application for AI Software Development Crew (LlamaIndex)
Provides a web interface to trigger and monitor build jobs
"""
import os
import json
import uuid
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional
from flask import Flask, render_template, request, jsonify, send_from_directory
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
    
    # Lazy import to avoid blocking Flask startup
    try:
        from src.llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow
    except Exception as imp_err:
        logger.exception(f"Failed to import SoftwareDevWorkflow: {imp_err}")
        job_db.mark_failed(job_id, f"Import error: {imp_err}")
        return
    
    logger = logging.getLogger(__name__)
    
    # Use global config if not provided
    if job_config is None:
        job_config = config
    
    def progress_callback(phase: str, progress: int, message: str = None):
        """Update job progress in real-time"""
        job_db.update_progress(job_id, phase, progress, message)
    
    try:
        # Mark job as started
        job_db.mark_started(job_id)
        
        # Get job to access workspace path
        job = job_db.get_job(job_id)
        if not job:
            logger.error(f"Job {job_id} not found in database during async run")
            return
        
        job_workspace = Path(job['workspace_path'])
        
        # Log to job-specific error log
        error_log_path = job_workspace / "crew_errors.log"
        
        # Set workspace path in environment for this job
        original_workspace = os.environ.get("WORKSPACE_PATH")
        os.environ["WORKSPACE_PATH"] = str(job_workspace)
        os.environ["PROJECT_ID"] = job_id
        
        try:
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
            
            # Log start
            with open(error_log_path, 'a') as f:
                f.write(f"\n{'='*80}\n")
                f.write(f"JOB STARTED - {datetime.now().isoformat()}\n")
                f.write(f"Vision: {vision}\n")
                if uploaded_docs:
                    f.write(f"Reference Docs: {', '.join(d['original_name'] for d in uploaded_docs)}\n")
                f.write(f"{'='*80}\n\n")
            
            progress_callback('initializing', 5, "Initializing workflow...")
            
            # Create workflow with job-specific workspace
            workflow = SoftwareDevWorkflow(
                project_id=job_id,
                workspace_path=job_workspace,
                vision=enriched_vision,
                config=job_config,
                progress_callback=progress_callback
            )
            
            progress_callback('meta', 10, "Starting Meta phase...")
            
            # Run workflow
            results = workflow.run()
            
            # Log completion
            with open(error_log_path, 'a') as f:
                f.write(f"\n{'='*80}\n")
                f.write(f"JOB COMPLETED SUCCESSFULLY - {datetime.now().isoformat()}\n")
                f.write(f"{'='*80}\n\n")
            
        except Exception as inner_e:
            # Log inner exception
            error_trace = traceback.format_exc()
            with open(error_log_path, 'a') as f:
                f.write(f"\n{'='*80}\n")
                f.write(f"ERROR IN WORKFLOW - {datetime.now().isoformat()}\n")
                f.write(f"{'='*80}\n")
                f.write(f"Error Type: {type(inner_e).__name__}\n")
                f.write(f"Error Message: {str(inner_e)}\n")
                f.write(f"Traceback:\n{error_trace}\n")
                f.write(f"{'='*80}\n\n")
            
            # Re-raise to be caught by outer exception handler
            raise
        finally:
            # Restore original workspace path
            if original_workspace:
                os.environ["WORKSPACE_PATH"] = original_workspace
            elif "WORKSPACE_PATH" in os.environ:
                del os.environ["WORKSPACE_PATH"]
        
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
        job_count = len(jobs)
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
    """Create a new build job. Accepts JSON or multipart/form-data with files."""
    # Support both JSON and multipart
    github_urls = []
    backend_name = 'opl-ai-team'  # default
    
    if request.content_type and 'multipart/form-data' in request.content_type:
        vision = request.form.get('vision', '')
        backend_name = request.form.get('backend', 'opl-ai-team')
        # GitHub URLs can come as repeated form fields
        github_urls = request.form.getlist('github_urls')
    else:
        data = request.json or {}
        vision = data.get('vision', '')
        backend_name = data.get('backend', 'opl-ai-team')
        github_urls = data.get('github_urls', [])
    
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
    
    # Save any uploaded documents
    uploaded_docs = []
    if request.files:
        files = request.files.getlist('documents')
        if len(files) > MAX_FILES_PER_JOB:
            files = files[:MAX_FILES_PER_JOB]
        uploaded_docs = _save_uploaded_files(job_id, job_workspace, files)
    
    # Process GitHub URLs with Repomix (in background thread to not block response)
    valid_urls = [u for u in github_urls if u and _is_github_url(u)]
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
# Ordered by workflow phase sequence
AGENT_DEFINITIONS = [
    {'name': 'Meta Agent',    'role': 'Orchestrator',      'model': 'deepseek-r1-distill-qwen-14b', 'phase': 'meta'},
    {'name': 'Product Owner', 'role': 'Requirements',      'model': 'qwen3-14b',                    'phase': 'product_owner'},
    {'name': 'Designer',      'role': 'UX/UI',             'model': 'granite-3-2-8b-instruct',      'phase': 'designer'},
    {'name': 'Tech Architect','role': 'System Design',     'model': 'qwen3-14b',                    'phase': 'tech_architect'},
    {'name': 'Dev Crew',      'role': 'Implementation',    'model': 'qwen3-14b',                    'phase': 'development'},
    {'name': 'Frontend Crew', 'role': 'UI Implementation', 'model': 'granite-3-2-8b-instruct',      'phase': 'frontend'},
]

PHASE_ORDER = [a['phase'] for a in AGENT_DEFINITIONS]


@app.route('/api/jobs/<job_id>/agents', methods=['GET'])
def get_job_agents(job_id):
    """Get agent statuses derived from job's current phase"""
    job = job_db.get_job(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404

    current_phase = job.get('current_phase', 'queued')
    job_status = job.get('status', 'queued')
    messages = job.get('last_message', [])

    # Determine the index of the current phase
    if job_status == 'completed' or current_phase == 'completed':
        current_idx = len(PHASE_ORDER)  # All phases completed
    elif current_phase in PHASE_ORDER:
        current_idx = PHASE_ORDER.index(current_phase)
    else:
        current_idx = -1  # Job hasn't started meaningful work yet

    agents = []
    for i, defn in enumerate(AGENT_DEFINITIONS):
        # Derive status
        if current_idx < 0:
            status = 'idle'
        elif i < current_idx:
            status = 'completed'
        elif i == current_idx:
            status = 'working'
        else:
            status = 'idle'

        # Find last activity message for this agent's phase
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
                # List files for specific job
                job_workspace = Path(job['workspace_path'])
            else:
                # Job not found, default to base workspace
                job_workspace = base_workspace_path
        else:
            # List files from all jobs
            job_workspace = base_workspace_path
        
        files = []
        if job_workspace.exists():
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
    port = int(os.getenv('PORT', 8080))
    # use_reloader=False is critical: the Werkzeug reloader holds an import
    # lock that deadlocks background threads doing lazy imports (e.g. the
    # SoftwareDevWorkflow import inside run_job_async).  It also kills
    # in-flight job threads on every file change.
    app.run(host='0.0.0.0', port=port, debug=True, use_reloader=False)
