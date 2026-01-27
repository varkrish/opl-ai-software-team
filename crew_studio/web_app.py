"""
Web GUI Application for AI Software Development Crew
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

from src.ai_software_dev_crew.orchestrator import SoftwareDevOrchestrator, run_orchestrator, review_codebase

# Load environment variables
load_dotenv()

# Get the directory of this file
current_dir = Path(__file__).parent
web_dir = current_dir

app = Flask(__name__, 
            static_folder=str(web_dir / 'static'),
            template_folder=str(web_dir / 'templates'))
CORS(app)

# Job storage (in-memory, can be replaced with Redis/DB)
jobs: Dict[str, Dict[str, Any]] = {}

# Base workspace path (contains job-specific folders)
base_workspace_path = Path(os.getenv("WORKSPACE_PATH", "./workspace"))
base_workspace_path.mkdir(parents=True, exist_ok=True)


def run_job_async(job_id: str, vision: str, design_specs_path: str = None, design_specs_urls: list = None):
    """Run orchestrator in a separate thread with job-specific workspace"""
    import traceback
    import logging
    
    logger = logging.getLogger(__name__)
    
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
        
        try:
            # Log start
            with open(error_log_path, 'a') as f:
                f.write(f"\n{'='*80}\n")
                f.write(f"JOB STARTED - {datetime.now().isoformat()}\n")
                f.write(f"Vision: {vision}\n")
                f.write(f"{'='*80}\n\n")
            
            progress_callback('initializing', 5, "Initializing orchestrator...")
            
            # Create orchestrator with job-specific workspace and progress callback
            orchestrator = SoftwareDevOrchestrator(workspace_path=job_workspace, progress_callback=progress_callback)
            
            progress_callback('requirements', 10, "Starting Requirements Analysis phase...")
            
            # Run orchestrator
            results = orchestrator.run(vision, design_specs_path, design_specs_urls)
            
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
                f.write(f"ERROR IN ORCHESTRATOR - {datetime.now().isoformat()}\n")
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
            'requirements': str(results.get('requirements', '')),
            'backend_implementation': str(results.get('backend_implementation', '')),
            'frontend_implementation': str(results.get('frontend_implementation', ''))
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
            'exceeded your current quota' in error_message.lower()
        )
        
        if is_quota_exhausted:
            jobs[job_id]['status'] = 'quota_exhausted'
            jobs[job_id]['error'] = (
                "❌ Daily API quota limit reached. "
                "The job has been stopped. "
                "Please check your API plan and billing details, or try again tomorrow. "
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


@app.route('/api/jobs', methods=['POST'])
def create_job():
    """Create a new build job"""
    data = request.json
    
    job_id = str(uuid.uuid4())
    vision = data.get('vision', '')
    design_specs_path = data.get('design_specs_path')
    design_specs_urls = data.get('design_specs_urls', [])
    
    if not vision:
        return jsonify({'error': 'Vision is required'}), 400
    
    # Create job-specific workspace folder
    job_workspace = base_workspace_path / f"job-{job_id}"
    job_workspace.mkdir(parents=True, exist_ok=True)
    
    # Create job record
    jobs[job_id] = {
        'id': job_id,
        'vision': vision,
        'design_specs_path': design_specs_path,
        'design_specs_urls': design_specs_urls,
        'workspace_path': str(job_workspace),
        'status': 'queued',
        'progress': 0,
        'current_phase': 'queued',
        'created_at': datetime.now().isoformat(),
        'started_at': None,
        'completed_at': None,
        'results': None,
        'error': None
    }
    
    # Start job in background thread
    thread = threading.Thread(
        target=run_job_async,
        args=(job_id, vision, design_specs_path, design_specs_urls)
    )
    thread.daemon = True
    thread.start()
    
    return jsonify({'job_id': job_id, 'status': 'queued'}), 201


@app.route('/api/jobs', methods=['GET'])
def list_jobs():
    """List all jobs"""
    return jsonify({
        'jobs': list(jobs.values()),
        'total': len(jobs)
    })


@app.route('/api/jobs/<job_id>', methods=['GET'])
def get_job(job_id):
    """Get job status"""
    if job_id not in jobs:
        return jsonify({'error': 'Job not found'}), 404
    
    return jsonify(jobs[job_id])


@app.route('/api/jobs/<job_id>/cancel', methods=['POST'])
def cancel_job(job_id):
    """Cancel a running job"""
    if job_id not in jobs:
        return jsonify({'error': 'Job not found'}), 404
    
    if jobs[job_id]['status'] in ['completed', 'failed', 'cancelled']:
        return jsonify({'error': 'Job cannot be cancelled'}), 400
    
    jobs[job_id]['status'] = 'cancelled'
    jobs[job_id]['completed_at'] = datetime.now().isoformat()
    
    return jsonify({'status': 'cancelled'})


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


@app.route('/api/jobs/<job_id>/tasks', methods=['GET'])
def get_job_tasks(job_id):
    """Get task status from SQLite database"""
    if job_id not in jobs:
        return jsonify({'error': 'Job not found'}), 404
    
    workspace_path = Path(jobs[job_id]['workspace_path'])
    # Search for any .db file in the workspace that starts with 'tasks_'
    db_files = list(workspace_path.glob('tasks_*.db'))
    
    if not db_files:
        return jsonify({'tasks': [], 'message': 'Task database not found'})
    
    db_path = db_files[0]
    
    try:
        from src.llamaindex_crew.orchestrator.task_manager import TaskManager
        task_manager = TaskManager(db_path, job_id)
        
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Get all tasks with their full info
        cursor.execute("""
            SELECT task_id, phase, task_type, description, status, required, created_at, started_at, completed_at, error_message
            FROM tasks
            ORDER BY created_at ASC
        """)
        
        tasks = []
        for row in cursor.fetchall():
            tasks.append({
                'task_id': row[0],
                'phase': row[1],
                'task_type': row[2],
                'description': row[3],
                'status': row[4],
                'required': bool(row[5]),
                'created_at': row[6],
                'started_at': row[7],
                'completed_at': row[8],
                'error_message': row[9]
            })
        
        # Get recent activity from log
        cursor.execute("""
            SELECT task_id, event_type, event_data, timestamp
            FROM task_execution_log
            ORDER BY timestamp DESC
            LIMIT 20
        """)
        
        activity = []
        for row in cursor.fetchall():
            activity.append({
                'task_id': row[0],
                'event_type': row[1],
                'event_data': json.loads(row[2]) if row[2] else {},
                'timestamp': row[3]
            })
            
        conn.close()
        
        return jsonify({
            'total_tasks': len(tasks),
            'tasks': tasks,
            'activity': activity
        })
    except Exception as e:
        return jsonify({'error': f'Could not read tasks: {str(e)}'}), 500


@app.route('/api/jobs/<job_id>/activity', methods=['GET'])
def get_job_activity(job_id: str):
    """Get activity log for a specific job"""
    if job_id not in jobs:
        return jsonify({'error': 'Job not found'}), 404
    
    job_workspace = base_workspace_path / f"job-{job_id}"
    activity_log_path = job_workspace / "activity.log"
    
    if activity_log_path.exists():
        try:
            with open(activity_log_path, 'r') as f:
                content = f.read()
            return jsonify({
                'activity_log': content,
                'last_updated': datetime.now().isoformat()
            })
        except Exception as e:
            return jsonify({'error': f'Could not read activity log: {str(e)}'}), 500
    else:
        return jsonify({
            'activity_log': 'Activity log not available yet.',
            'last_updated': datetime.now().isoformat()
        })


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


def run_web_app(host='0.0.0.0', port=5000, debug=False):
    """Run the web application"""
    print(f"\n🚀 Starting AI Software Development Crew Web GUI")
    print(f"   Access at: http://{host if host != '0.0.0.0' else 'localhost'}:{port}")
    print(f"   Workspace: {base_workspace_path.absolute()}\n")
    
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == '__main__':
    run_web_app(debug=True)

