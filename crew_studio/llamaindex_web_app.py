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

from src.llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow
from src.llamaindex_crew.config import ConfigLoader, SecretConfig

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

# Job storage (in-memory, can be replaced with Redis/DB)
jobs: Dict[str, Dict[str, Any]] = {}

# Base workspace path (contains job-specific folders)
base_workspace_path = Path(os.getenv("WORKSPACE_PATH", "./workspace"))
base_workspace_path.mkdir(parents=True, exist_ok=True)


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
        if job_id in jobs:
            jobs[job_id]['current_phase'] = phase
            jobs[job_id]['progress'] = progress
            if message:
                # Store last message for display
                if 'last_message' not in jobs[job_id]:
                    jobs[job_id]['last_message'] = []
                jobs[job_id]['last_message'].append({
                    'timestamp': datetime.now().isoformat(),
                    'phase': phase,
                    'message': message
                })
                # Keep only last 50 messages
                if len(jobs[job_id]['last_message']) > 50:
                    jobs[job_id]['last_message'] = jobs[job_id]['last_message'][-50:]
    
    try:
        jobs[job_id]['status'] = 'running'
        jobs[job_id]['started_at'] = datetime.now().isoformat()
        jobs[job_id]['current_phase'] = 'initializing'
        jobs[job_id]['progress'] = 0
        jobs[job_id]['last_message'] = []
        
        # Create job-specific workspace folder
        job_workspace = base_workspace_path / f"job-{job_id}"
        job_workspace.mkdir(parents=True, exist_ok=True)
        jobs[job_id]['workspace_path'] = str(job_workspace)
        
        # Log to job-specific error log
        error_log_path = job_workspace / "crew_errors.log"
        
        # Set workspace path in environment for this job
        original_workspace = os.environ.get("WORKSPACE_PATH")
        os.environ["WORKSPACE_PATH"] = str(job_workspace)
        os.environ["PROJECT_ID"] = job_id
        
        try:
            # Log start
            with open(error_log_path, 'a') as f:
                f.write(f"\n{'='*80}\n")
                f.write(f"JOB STARTED - {datetime.now().isoformat()}\n")
                f.write(f"Vision: {vision}\n")
                f.write(f"{'='*80}\n\n")
            
            progress_callback('initializing', 5, "Initializing workflow...")
            
            # Create workflow with job-specific workspace
            workflow = SoftwareDevWorkflow(
                project_id=job_id,
                workspace_path=job_workspace,
                vision=vision,
                config=job_config
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
        
        # Update job status
        jobs[job_id]['status'] = 'completed'
        jobs[job_id]['completed_at'] = datetime.now().isoformat()
        jobs[job_id]['results'] = {
            'status': results.get('status', 'completed'),
            'budget_report': results.get('budget_report', {}),
            'task_validation': results.get('task_validation', {})
        }
        jobs[job_id]['progress'] = 100
        jobs[job_id]['current_phase'] = 'completed'
        
    except Exception as e:
        error_message = str(e)
        error_trace = traceback.format_exc()
        
        # Log to error file
        try:
            error_log_path = base_workspace_path / f"job-{job_id}" / "crew_errors.log"
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
            jobs[job_id]['status'] = 'quota_exhausted'
            jobs[job_id]['error'] = (
                "❌ API quota limit reached. "
                "The job has been stopped. "
                "Please check your API plan and billing details, or try again later. "
                f"\n\nDetails: {error_message[:500]}"
            )
        else:
            jobs[job_id]['status'] = 'failed'
            jobs[job_id]['error'] = f"{error_message}\n\nFull traceback available in crew_errors.log"
        
        jobs[job_id]['completed_at'] = datetime.now().isoformat()
        jobs[job_id]['current_phase'] = 'error'


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


@app.route('/api/jobs', methods=['POST'])
def create_job():
    """Create a new build job"""
    data = request.json
    
    job_id = str(uuid.uuid4())
    vision = data.get('vision', '')
    
    if not vision:
        return jsonify({'error': 'Vision is required'}), 400
    
    # Create job record
    jobs[job_id] = {
        'id': job_id,
        'vision': vision,
        'workspace_path': str(base_workspace_path / f"job-{job_id}"),
        'status': 'queued',
        'progress': 0,
        'current_phase': 'queued',
        'created_at': datetime.now().isoformat(),
        'started_at': None,
        'completed_at': None,
        'results': None,
        'error': None
    }
    
    # Start job in background thread with config
    thread = threading.Thread(
        target=run_job_async,
        args=(job_id, vision, config)
    )
    thread.daemon = True
    thread.start()
    
    return jsonify({'job_id': job_id, 'status': 'queued'}), 201


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
            for job in jobs.values()
        ]
    })


@app.route('/api/jobs/<job_id>', methods=['GET'])
def get_job(job_id):
    """Get job status and details"""
    if job_id not in jobs:
        return jsonify({'error': 'Job not found'}), 404
    
    job = jobs[job_id]
    return jsonify(job)


@app.route('/api/jobs/<job_id>/progress', methods=['GET'])
def get_job_progress(job_id):
    """Get job progress"""
    if job_id not in jobs:
        return jsonify({'error': 'Job not found'}), 404
    
    job = jobs[job_id]
    return jsonify({
        'status': job['status'],
        'progress': job['progress'],
        'current_phase': job['current_phase'],
        'last_message': job.get('last_message', [])[-10:]  # Last 10 messages
    })


@app.route('/api/jobs/<job_id>/files', methods=['GET'])
def list_job_files(job_id):
    """List files generated by job"""
    if job_id not in jobs:
        return jsonify({'error': 'Job not found'}), 404
    
    workspace_path = Path(jobs[job_id]['workspace_path'])
    
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
    """Get task status from SQLite database"""
    import sqlite3
    if job_id not in jobs:
        return jsonify({'error': 'Job not found'}), 404
    
    workspace_path = Path(jobs[job_id]['workspace_path'])
    db_path = workspace_path / f"tasks_{job_id}.db"
    
    if not db_path.exists():
        # Try to find any tasks_*.db file
        db_files = list(workspace_path.glob('tasks_*.db'))
        if db_files:
            db_path = db_files[0]
        else:
            return jsonify({'tasks': [], 'message': 'Task database not found'})
    
    try:
        from src.llamaindex_crew.orchestrator.task_manager import TaskManager
        task_manager = TaskManager(db_path, job_id)
        
        all_tasks = task_manager.get_all_tasks()
        
        # Get phase status
        phase_stats = {}
        for task in all_tasks:
            phase = task.phase
            if phase not in phase_stats:
                phase_stats[phase] = {'total': 0, 'completed': 0, 'in_progress': 0}
            
            phase_stats[phase]['total'] += 1
            status = task_manager.get_task_status(task.task_id)
            if status and status.value == 'completed':
                phase_stats[phase]['completed'] += 1
            elif status and status.value == 'in_progress':
                phase_stats[phase]['in_progress'] += 1
        
        return jsonify({
            'total_tasks': len(all_tasks),
            'phase_stats': phase_stats,
            'tasks': [
                {
                    'task_id': task.task_id,
                    'phase': task.phase,
                    'task_type': task.task_type,
                    'description': task.description,
                    'status': task_manager.get_task_status(task.task_id).value if task_manager.get_task_status(task.task_id) else 'unknown'
                }
                for task in all_tasks
            ]
        })
    except Exception as e:
        import traceback
        print(f"Error reading tasks: {e}")
        traceback.print_exc()
        return jsonify({'error': f'Could not read tasks: {str(e)}'}), 500


@app.route('/api/jobs/<job_id>/budget', methods=['GET'])
def get_job_budget(job_id):
    """Get budget report for job"""
    if job_id not in jobs:
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
    total_jobs = len(jobs)
    completed = sum(1 for j in jobs.values() if j['status'] == 'completed')
    running = sum(1 for j in jobs.values() if j['status'] == 'running')
    failed = sum(1 for j in jobs.values() if j['status'] == 'failed')
    quota_exhausted = sum(1 for j in jobs.values() if j['status'] == 'quota_exhausted')
    
    return jsonify({
        'total_jobs': total_jobs,
        'completed': completed,
        'running': running,
        'failed': failed,
        'quota_exhausted': quota_exhausted,
        'queued': sum(1 for j in jobs.values() if j['status'] == 'queued')
    })


@app.route('/api/workspace/files', methods=['GET'])
def list_workspace_files():
    """List files in workspace (all jobs or specific job)"""
    try:
        job_id = request.args.get('job_id')
        
        if job_id and job_id in jobs:
            # List files for specific job
            job_workspace = Path(jobs[job_id].get('workspace_path', base_workspace_path / f"job-{job_id}"))
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
        
        if job_id and job_id in jobs:
            # Get file from specific job workspace
            job_workspace = Path(jobs[job_id].get('workspace_path', base_workspace_path / f"job-{job_id}"))
            full_path = job_workspace / file_path
        else:
            # Try to find file in any job workspace
            full_path = None
            for jid, job in jobs.items():
                job_workspace = Path(job.get('workspace_path', base_workspace_path / f"job-{jid}"))
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
    if job_id not in jobs:
        return jsonify({'error': 'Job not found'}), 404
    
    if jobs[job_id]['status'] in ['completed', 'failed', 'cancelled']:
        return jsonify({'error': 'Job is not running'}), 400
    
    jobs[job_id]['status'] = 'cancelled'
    jobs[job_id]['completed_at'] = datetime.now().isoformat()
    
    return jsonify({'status': 'cancelled'})


if __name__ == '__main__':
    port = int(os.getenv('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=True)
