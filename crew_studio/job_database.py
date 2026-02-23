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
            # ── Migration issues table ────────────────────────────────────
            conn.execute("""
                CREATE TABLE IF NOT EXISTS migration_issues (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    migration_id TEXT NOT NULL,
                    title TEXT,
                    severity TEXT,
                    effort TEXT,
                    files TEXT,
                    description TEXT,
                    migration_hint TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    error TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY (job_id) REFERENCES jobs(id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_migration_issues_job ON migration_issues(job_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_migration_issues_migration ON migration_issues(migration_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_migration_issues_status ON migration_issues(status)")
            # ── Refactor tasks table ──────────────────────────────────────
            conn.execute("""
                CREATE TABLE IF NOT EXISTS refactor_tasks (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    action TEXT NOT NULL,
                    instruction TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    error TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY (job_id) REFERENCES jobs(id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_refactor_tasks_job ON refactor_tasks(job_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_refactor_tasks_status ON refactor_tasks(status)")
    
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

    _SORTABLE_COLUMNS = {"created_at", "vision", "status", "progress", "current_phase"}

    def _build_where(
        self,
        vision_filter: Optional[str] = None,
        status_filter: Optional[str] = None,
    ) -> tuple:
        """Build WHERE clause and params from optional filters."""
        clauses: list = []
        params: list = []
        if vision_filter:
            clauses.append("vision LIKE ?")
            params.append(f"%{vision_filter}%")
        if status_filter:
            clauses.append("status = ?")
            params.append(status_filter)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        return where, params

    def get_jobs_count(
        self,
        vision_filter: Optional[str] = None,
        status_filter: Optional[str] = None,
    ) -> int:
        """Return total number of jobs, optionally filtered."""
        where, params = self._build_where(vision_filter, status_filter)
        with self._get_conn() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) FROM jobs{where}", params
            ).fetchone()
            return row[0] if row else 0

    def get_jobs_paginated(
        self,
        limit: int = 10,
        offset: int = 0,
        vision_filter: Optional[str] = None,
        status_filter: Optional[str] = None,
        sort_by: Optional[str] = None,
        sort_order: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get jobs with pagination, optional filters, and sorting."""
        where, params = self._build_where(vision_filter, status_filter)

        col = sort_by if sort_by in self._SORTABLE_COLUMNS else "created_at"
        direction = "ASC" if sort_order == "asc" else "DESC"
        collate = " COLLATE NOCASE" if col == "vision" else ""

        sql = f"SELECT * FROM jobs{where} ORDER BY {col}{collate} {direction} LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self._get_conn() as conn:
            rows = conn.execute(sql, params).fetchall()
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
            conn.execute("DELETE FROM migration_issues WHERE job_id = ?", (job_id,))
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

    # ── Migration methods ────────────────────────────────────────────────────

    def create_migration_issue(
        self,
        issue_id: str,
        job_id: str,
        migration_id: str,
        title: str,
        severity: str,
        effort: str,
        files: List,
        description: str,
        migration_hint: str,
    ) -> Dict[str, Any]:
        """Create a migration issue record (status=pending)."""
        now = datetime.now().isoformat()
        files_json = json.dumps(files) if isinstance(files, (list, tuple)) else files
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO migration_issues
                    (id, job_id, migration_id, title, severity, effort,
                     files, description, migration_hint, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """, (issue_id, job_id, migration_id, title, severity, effort,
                  files_json, description, migration_hint, now))
        return {
            'id': issue_id,
            'job_id': job_id,
            'migration_id': migration_id,
            'title': title,
            'severity': severity,
            'effort': effort,
            'files': files_json,
            'description': description,
            'migration_hint': migration_hint,
            'status': 'pending',
            'error': None,
            'created_at': now,
            'completed_at': None,
        }

    def update_migration_issue_status(
        self, issue_id: str, status: str, error: Optional[str] = None
    ) -> bool:
        """Update a migration issue's status. Sets completed_at on terminal states."""
        updates = {'status': status}
        if error is not None:
            updates['error'] = error
        if status in ('completed', 'failed', 'skipped'):
            updates['completed_at'] = datetime.now().isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [issue_id]
        with self._get_conn() as conn:
            cursor = conn.execute(
                f"UPDATE migration_issues SET {set_clause} WHERE id = ?", values
            )
            return cursor.rowcount > 0

    def get_migration_issues(
        self, job_id: str, migration_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Return migration issues for a job, optionally filtered by migration_id."""
        with self._get_conn() as conn:
            if migration_id:
                rows = conn.execute(
                    "SELECT * FROM migration_issues WHERE job_id = ? AND migration_id = ? ORDER BY created_at",
                    (job_id, migration_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM migration_issues WHERE job_id = ? ORDER BY created_at",
                    (job_id,),
                ).fetchall()
            return [dict(r) for r in rows]

    def get_migration_summary(self, job_id: str) -> Dict[str, int]:
        """Return aggregated counts of migration issues by status."""
        with self._get_conn() as conn:
            row = conn.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending,
                    SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) as running,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed,
                    SUM(CASE WHEN status = 'skipped' THEN 1 ELSE 0 END) as skipped
                FROM migration_issues
                WHERE job_id = ?
            """, (job_id,)).fetchone()
            return {
                'total': row['total'] or 0,
                'pending': row['pending'] or 0,
                'running': row['running'] or 0,
                'completed': row['completed'] or 0,
                'failed': row['failed'] or 0,
                'skipped': row['skipped'] or 0,
            }

    def fail_stale_migrations(self, job_id: str) -> int:
        """Mark any migration_issues still in 'running' or 'pending' state as 'failed'.

        Returns the number of rows updated.  Called before restarting a
        migration job or during startup cleanup.  Pending issues are included
        because they were queued by the previous (now-dead) run and won't
        be picked up unless they go through the failed→retry path.
        """
        now = datetime.now().isoformat()
        with self._get_conn() as conn:
            cursor = conn.execute(
                "UPDATE migration_issues "
                "SET status = 'failed', error = 'Stale — cleared on restart', completed_at = ? "
                "WHERE job_id = ? AND status IN ('running', 'pending')",
                (now, job_id),
            )
            return cursor.rowcount

    def get_failed_migration_issues(self, job_id: str) -> List[Dict[str, Any]]:
        """Return migration issues with status='failed' for the given job."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM migration_issues WHERE job_id = ? AND status = 'failed' ORDER BY created_at",
                (job_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def reset_failed_migration_issues(self, job_id: str) -> int:
        """Reset failed migration issues back to 'pending' so they can be retried.

        Clears the error and completed_at fields. Returns the number of rows reset.
        """
        with self._get_conn() as conn:
            cursor = conn.execute(
                "UPDATE migration_issues "
                "SET status = 'pending', error = NULL, completed_at = NULL "
                "WHERE job_id = ? AND status = 'failed'",
                (job_id,),
            )
            return cursor.rowcount

    def delete_migration_issues(self, job_id: str) -> int:
        """Delete ALL migration issues for a job (used before a clean re-run)."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM migration_issues WHERE job_id = ?",
                (job_id,),
            )
            return cursor.rowcount

    def get_running_migration(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Return the currently running migration issue for this job, if any."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM migration_issues WHERE job_id = ? AND status = 'running' ORDER BY created_at DESC LIMIT 1",
                (job_id,),
            ).fetchone()
            return dict(row) if row else None

    # ── Refactor Tasks ───────────────────────────────────────────

    def create_refactor_task(
        self,
        task_id: str,
        job_id: str,
        file_path: str,
        action: str,
        instruction: str,
    ) -> Dict[str, Any]:
        """Create a new refactor task record (status=pending)."""
        now = datetime.now().isoformat()
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO refactor_tasks (id, job_id, file_path, action, instruction, status, created_at)
                VALUES (?, ?, ?, ?, ?, 'pending', ?)
                """,
                (task_id, job_id, file_path, action, instruction, now),
            )
        return {
            "id": task_id,
            "job_id": job_id,
            "file_path": file_path,
            "action": action,
            "instruction": instruction,
            "status": "pending",
            "created_at": now,
        }

    def update_refactor_task_status(
        self, task_id: str, status: str, error: Optional[str] = None
    ) -> bool:
        """Update a refactor task's status. Sets completed_at on terminal states."""
        now = datetime.now().isoformat() if status in ("completed", "failed", "skipped") else None
        with self._get_conn() as conn:
            cursor = conn.execute(
                """
                UPDATE refactor_tasks
                SET status = ?, error = ?, completed_at = COALESCE(?, completed_at)
                WHERE id = ?
                """,
                (status, error, now, task_id),
            )
            return cursor.rowcount > 0

    def get_refactor_tasks(self, job_id: str) -> List[Dict[str, Any]]:
        """Return all refactor tasks for a job."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM refactor_tasks WHERE job_id = ? ORDER BY created_at ASC",
                (job_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_refactor_summary(self, job_id: str) -> Dict[str, int]:
        """Return aggregated counts of refactor tasks by status."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) as count FROM refactor_tasks WHERE job_id = ? GROUP BY status",
                (job_id,),
            ).fetchall()
            
            summary = {"total": 0, "pending": 0, "running": 0, "completed": 0, "failed": 0, "skipped": 0}
            for row in rows:
                status = row["status"]
                count = row["count"]
                if status in summary:
                    summary[status] = count
                summary["total"] += count
            return summary

    def get_running_refactor_task(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Return the currently running refactor task for this job, if any."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM refactor_tasks WHERE job_id = ? AND status = 'running' ORDER BY created_at DESC LIMIT 1",
                (job_id,),
            ).fetchone()
            return dict(row) if row else None

    def fail_stale_refactor_tasks(self, job_id: str, error: str = "Interrupted") -> int:
        """Mark any 'running' refactor tasks as failed (e.g. after a crash)."""
        now = datetime.now().isoformat()
        with self._get_conn() as conn:
            cursor = conn.execute(
                """
                UPDATE refactor_tasks
                SET status = 'failed', error = ?, completed_at = ?
                WHERE job_id = ? AND status = 'running'
                """,
                (error, now, job_id),
            )
            return cursor.rowcount

    def delete_refactor_tasks(self, job_id: str) -> int:
        """Delete ALL refactor tasks for a job."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM refactor_tasks WHERE job_id = ?",
                (job_id,),
            )
            return cursor.rowcount


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
