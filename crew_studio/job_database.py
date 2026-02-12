"""
Centralized SQLite database for persistent job management across workspaces.
Stores all job metadata, status, progress, and messages.
"""
import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
from contextlib import contextmanager


class JobDatabase:
    """Manages persistent job storage in a centralized SQLite database."""
    
    def __init__(self, db_path: Path):
        """Initialize database connection and ensure schema exists."""
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
    
    @contextmanager
    def _get_conn(self):
        """Get a database connection with automatic commit/rollback."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    
    def _init_schema(self):
        """Create jobs and documents tables if they don't exist."""
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    vision TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress INTEGER DEFAULT 0,
                    current_phase TEXT DEFAULT 'queued',
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    workspace_path TEXT NOT NULL,
                    results TEXT,
                    error TEXT,
                    last_message TEXT DEFAULT '[]'
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    original_name TEXT NOT NULL,
                    file_type TEXT NOT NULL,
                    file_size INTEGER NOT NULL,
                    stored_path TEXT NOT NULL,
                    uploaded_at TEXT NOT NULL,
                    FOREIGN KEY (job_id) REFERENCES jobs(id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON jobs(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_created_at ON jobs(created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_doc_job ON documents(job_id)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS refinements (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    file_path TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    error TEXT,
                    FOREIGN KEY (job_id) REFERENCES jobs(id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_refinements_job ON refinements(job_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_refinements_status ON refinements(status)")
    
    def create_job(self, job_id: str, vision: str, workspace_path: str) -> Dict[str, Any]:
        """Create a new job record."""
        now = datetime.now().isoformat()
        job = {
            'id': job_id,
            'vision': vision,
            'status': 'queued',
            'progress': 0,
            'current_phase': 'queued',
            'created_at': now,
            'started_at': None,
            'completed_at': None,
            'workspace_path': workspace_path,
            'results': None,
            'error': None,
            'last_message': '[]'
        }
        
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO jobs (id, vision, status, progress, current_phase, 
                                created_at, workspace_path, last_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                job['id'], job['vision'], job['status'], job['progress'],
                job['current_phase'], job['created_at'], job['workspace_path'],
                job['last_message']
            ))
        
        return job
    
    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get a single job by ID."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not row:
                return None
            return self._row_to_dict(row)
    
    def get_all_jobs(self) -> List[Dict[str, Any]]:
        """Get all jobs ordered by creation time (newest first)."""
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
            return [self._row_to_dict(row) for row in rows]
    
    def update_job(self, job_id: str, updates: Dict[str, Any]) -> bool:
        """Update job fields. Returns True if job was found and updated."""
        if not updates:
            return False
        
        # Build dynamic UPDATE query
        set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
        values = list(updates.values()) + [job_id]
        
        with self._get_conn() as conn:
            cursor = conn.execute(
                f"UPDATE jobs SET {set_clause} WHERE id = ?",
                values
            )
            return cursor.rowcount > 0

    def delete_job(self, job_id: str) -> bool:
        """Delete a job and its related records. Returns True if job was found and deleted."""
        with self._get_conn() as conn:
            conn.execute("DELETE FROM refinements WHERE job_id = ?", (job_id,))
            conn.execute("DELETE FROM documents WHERE job_id = ?", (job_id,))
            cursor = conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            return cursor.rowcount > 0

    def update_progress(self, job_id: str, phase: str, progress: int, message: str = None):
        """Update job progress and optionally append a message."""
        job = self.get_job(job_id)
        if not job:
            return False
        
        updates = {
            'current_phase': phase,
            'progress': progress
        }
        
        if message:
            # Parse existing messages, append new one, keep last 50
            try:
                messages = json.loads(job.get('last_message', '[]'))
            except (json.JSONDecodeError, TypeError):
                messages = []
            
            messages.append({
                'timestamp': datetime.now().isoformat(),
                'phase': phase,
                'message': message
            })
            messages = messages[-50:]  # Keep last 50 messages
            updates['last_message'] = json.dumps(messages)
        
        return self.update_job(job_id, updates)
    
    def mark_started(self, job_id: str):
        """Mark job as started (running)."""
        return self.update_job(job_id, {
            'status': 'running',
            'started_at': datetime.now().isoformat(),
            'current_phase': 'initializing',
            'progress': 0,
            'last_message': '[]'
        })
    
    def mark_completed(self, job_id: str, results: Optional[Dict[str, Any]] = None):
        """Mark job as completed successfully."""
        updates = {
            'status': 'completed',
            'progress': 100,
            'current_phase': 'completed',
            'completed_at': datetime.now().isoformat()
        }
        if results:
            updates['results'] = json.dumps(results)
        return self.update_job(job_id, updates)
    
    def mark_failed(self, job_id: str, error: str):
        """Mark job as failed with error message."""
        return self.update_job(job_id, {
            'status': 'failed',
            'completed_at': datetime.now().isoformat(),
            'current_phase': 'error',
            'error': error
        })
    
    def mark_cancelled(self, job_id: str):
        """Mark job as cancelled by user."""
        return self.update_job(job_id, {
            'status': 'cancelled',
            'completed_at': datetime.now().isoformat()
        })
    
    def get_stats(self) -> Dict[str, int]:
        """Get aggregate statistics across all jobs."""
        with self._get_conn() as conn:
            cursor = conn.execute("""
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
                    SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) as running,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed,
                    SUM(CASE WHEN status = 'quota_exhausted' THEN 1 ELSE 0 END) as quota_exhausted,
                    SUM(CASE WHEN status = 'queued' THEN 1 ELSE 0 END) as queued
                FROM jobs
            """)
            row = cursor.fetchone()
            return {
                'total_jobs': row['total'] or 0,
                'completed': row['completed'] or 0,
                'running': row['running'] or 0,
                'failed': row['failed'] or 0,
                'quota_exhausted': row['quota_exhausted'] or 0,
                'queued': row['queued'] or 0
            }
    
    # ── Document Methods ───────────────────────────────────────────────────────

    def add_document(self, doc_id: str, job_id: str, filename: str,
                     original_name: str, file_type: str, file_size: int,
                     stored_path: str) -> Dict[str, Any]:
        """Record an uploaded document for a job."""
        now = datetime.now().isoformat()
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO documents (id, job_id, filename, original_name,
                                       file_type, file_size, stored_path, uploaded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (doc_id, job_id, filename, original_name, file_type,
                  file_size, stored_path, now))
        return {
            'id': doc_id, 'job_id': job_id, 'filename': filename,
            'original_name': original_name, 'file_type': file_type,
            'file_size': file_size, 'stored_path': stored_path,
            'uploaded_at': now,
        }

    def get_job_documents(self, job_id: str) -> List[Dict[str, Any]]:
        """Get all documents attached to a job."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM documents WHERE job_id = ? ORDER BY uploaded_at",
                (job_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def delete_document(self, doc_id: str) -> bool:
        """Delete a document record. Returns True if found."""
        with self._get_conn() as conn:
            cursor = conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
            return cursor.rowcount > 0

    # ── Refinement methods ─────────────────────────────────────────────────────

    def create_refinement(
        self,
        refinement_id: str,
        job_id: str,
        prompt: str,
        file_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a new refinement record (status=running)."""
        now = datetime.now().isoformat()
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO refinements (id, job_id, prompt, file_path, status, created_at)
                VALUES (?, ?, ?, ?, 'running', ?)
            """, (refinement_id, job_id, prompt, file_path, now))
        return {
            'id': refinement_id,
            'job_id': job_id,
            'prompt': prompt,
            'file_path': file_path,
            'status': 'running',
            'created_at': now,
            'completed_at': None,
            'error': None,
        }

    def complete_refinement(self, refinement_id: str) -> bool:
        """Mark refinement as completed."""
        now = datetime.now().isoformat()
        with self._get_conn() as conn:
            cursor = conn.execute(
                "UPDATE refinements SET status = 'completed', completed_at = ? WHERE id = ?",
                (now, refinement_id)
            )
            return cursor.rowcount > 0

    def fail_refinement(self, refinement_id: str, error: str) -> bool:
        """Mark refinement as failed with error message."""
        now = datetime.now().isoformat()
        with self._get_conn() as conn:
            cursor = conn.execute(
                "UPDATE refinements SET status = 'failed', completed_at = ?, error = ? WHERE id = ?",
                (now, error, refinement_id)
            )
            return cursor.rowcount > 0

    def get_running_refinement(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Return the running refinement for this job, if any."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM refinements WHERE job_id = ? AND status = 'running' ORDER BY created_at DESC LIMIT 1",
                (job_id,)
            ).fetchone()
            if not row:
                return None
            return dict(row)

    def get_refinement_history(self, job_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Return past refinements for this job (newest first)."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM refinements WHERE job_id = ? ORDER BY created_at DESC LIMIT ?",
                (job_id, limit)
            ).fetchall()
            return [dict(r) for r in rows]

    def _row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        """Convert SQLite row to dictionary, parsing JSON fields."""
        job = dict(row)
        
        # Parse JSON fields
        if job.get('results'):
            try:
                job['results'] = json.loads(job['results'])
            except (json.JSONDecodeError, TypeError):
                job['results'] = None
        
        if job.get('last_message'):
            try:
                job['last_message'] = json.loads(job['last_message'])
            except (json.JSONDecodeError, TypeError):
                job['last_message'] = []
        else:
            job['last_message'] = []
        
        return job
