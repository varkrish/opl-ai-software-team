"""
Centralized SQLite database for persistent job management across workspaces.
Stores all job metadata, status, progress, and messages.
"""
import sqlite3
import json
import os
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
        """Get a database connection with automatic commit/rollback.

        Uses WAL journal mode and a generous busy_timeout so concurrent
        readers never block writers and vice-versa.
        """
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
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
                    last_message TEXT DEFAULT '[]',
                    metadata TEXT DEFAULT '{}'
                )
            """)
            # Add metadata column to existing databases
            try:
                conn.execute("ALTER TABLE jobs ADD COLUMN metadata TEXT DEFAULT '{}'")
            except sqlite3.OperationalError:
                pass  # column already exists

            # Add new authentication columns to existing databases
            for col, col_type in [("owner_id", "TEXT"), ("owner_email", "TEXT"), ("team_id", "TEXT")]:
                try:
                    conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {col_type}")
                except sqlite3.OperationalError:
                    pass  # column already exists
            conn.execute("CREATE INDEX IF NOT EXISTS idx_owner ON jobs(owner_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_team ON jobs(team_id)")

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
            # ── Validation issues table ───────────────────────────────────
            conn.execute("""
                CREATE TABLE IF NOT EXISTS llm_usage (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    agent_name TEXT,
                    model TEXT,
                    input_tokens INTEGER DEFAULT 0,
                    output_tokens INTEGER DEFAULT 0,
                    cost REAL DEFAULT 0.0,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (job_id) REFERENCES jobs(id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_usage_job ON llm_usage(job_id)")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS validation_issues (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    check_name TEXT NOT NULL,
                    severity TEXT NOT NULL DEFAULT 'error',
                    file_path TEXT,
                    line_number INTEGER,
                    description TEXT NOT NULL,
                    fix_strategy TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    error TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY (job_id) REFERENCES jobs(id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_validation_issues_job ON validation_issues(job_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_validation_issues_status ON validation_issues(status)")

            # Per-user Jira configurations (tokens encrypted at rest)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jira_configs (
                    owner_id TEXT PRIMARY KEY,
                    jira_base_url TEXT NOT NULL,
                    jira_email TEXT NOT NULL,
                    encrypted_token TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            # Per-user GitHub configurations (PAT encrypted at rest)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS github_configs (
                    owner_id TEXT PRIMARY KEY,
                    github_username TEXT,
                    encrypted_token TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            # Generic key-value store for system-level settings
            # (e.g. auto-generated encryption keys)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS system_config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)

            # Per-user LLM configurations (api_key encrypted at rest)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_llm_configs (
                    owner_id TEXT PRIMARY KEY,
                    api_base_url TEXT NOT NULL,
                    encrypted_key TEXT NOT NULL,
                    model_manager TEXT NOT NULL DEFAULT 'gpt-4o-mini',
                    model_worker TEXT NOT NULL DEFAULT 'gpt-4o-mini',
                    model_reviewer TEXT NOT NULL DEFAULT 'gpt-4o-mini',
                    updated_at TEXT NOT NULL
                )
            """)

            # Model context windows dictionary (maps model substrings to context sizes)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS model_context_windows (
                    model_pattern TEXT PRIMARY KEY,
                    context_window INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            # Pre-populate defaults
            now = datetime.now().isoformat()
            default_models = [
                # OpenAI
                ("gpt-4o-mini", 128000),
                ("gpt-4o", 128000),
                ("gpt-4-turbo", 128000),
                ("gpt-4", 8192),
                ("gpt-3.5", 16384),
                # Anthropic (standard & OpenRouter)
                ("claude-3.5", 200000),
                ("claude-3-5", 200000),
                ("claude-3", 200000),
                # Google Gemini (standard & OpenRouter)
                ("gemini-2.0", 1000000),
                ("gemini-1.5", 1000000),
                ("gemini-pro", 2000000),
                ("gemini-flash", 1000000),
                # DeepSeek
                ("deepseek-r1", 128000),
                ("deepseek-v3", 128000),
                ("deepseek-chat", 128000),
                ("deepseek-coder", 128000),
                ("deepseek", 128000),
                # Meta Llama (standard & OpenRouter)
                ("llama-3.1", 128000),
                ("llama-3.2", 128000),
                ("llama-3", 8192),
                ("llama3", 8192),
                ("llama-scout", 400000),
                ("llama-nemotron", 10240),
                ("nemotron-3-super", 1000000),
                ("nemotron-3", 1000000),
                # IBM Granite
                ("granite-3", 128000),
                ("granite3", 128000),
                # Alibaba Qwen
                ("qwen-2.5", 128000),
                ("qwen-2", 32768),
                ("qwen3-coder", 400000),
                ("qwen3", 400000),
                # Mistral / Mixtral
                ("mixtral-8x22b", 64000),
                ("mixtral-8x7b", 32768),
                ("pixtral", 128000),
                ("mistral-large", 128000),
                ("mistral", 32768),
                ("mixtral", 32768),
                # Cohere
                ("command-r-plus", 128000),
                ("command-r", 128000),
                # Microsoft Phi
                ("phi-4", 16384),
                ("phi-3", 128000),
                ("codellama", 4000),
                # Common OpenRouter Free Models
                ("gemma-2", 8192),
                ("gemma", 8192),
                ("openchat", 8192),
                ("mythomax", 4096),
                ("hermes-3", 128000),
                ("zephyr", 16384)
            ]
            for pattern, window in default_models:
                conn.execute("""
                    INSERT OR IGNORE INTO model_context_windows (model_pattern, context_window, updated_at)
                    VALUES (?, ?, ?)
                """, (pattern, window, now))
    
    def create_job(self, job_id: str, vision: str, workspace_path: str,
                   metadata: Optional[Dict[str, Any]] = None,
                   owner_id: Optional[str] = None,
                   owner_email: Optional[str] = None,
                   team_id: Optional[str] = None) -> Dict[str, Any]:
        """Create a new job record."""
        now = datetime.now().isoformat()
        meta_json = json.dumps(metadata) if metadata else '{}'
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
            'last_message': '[]',
            'metadata': metadata or {},
            'owner_id': owner_id,
            'owner_email': owner_email,
            'team_id': team_id,
        }
        
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO jobs (id, vision, status, progress, current_phase, 
                                created_at, workspace_path, last_message, metadata,
                                owner_id, owner_email, team_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                job['id'], job['vision'], job['status'], job['progress'],
                job['current_phase'], job['created_at'], job['workspace_path'],
                job['last_message'], meta_json,
                job['owner_id'], job['owner_email'], job['team_id']
            ))
        
        return job
    
    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get a single job by ID."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT *, (SELECT SUM(cost) FROM llm_usage WHERE job_id = jobs.id) as cost, "
                "(SELECT SUM(input_tokens + output_tokens) FROM llm_usage WHERE job_id = jobs.id) as tokens "
                "FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if not row:
                return None
            return self._row_to_dict(row)
    
    def get_all_jobs(self, owner_id: Optional[str] = None,
                     team_ids: Optional[List[str]] = None,
                     is_admin: bool = False) -> List[Dict[str, Any]]:
        """Get all jobs ordered by creation time (newest first), scoped by access."""
        where, params = self._build_where(owner_id=owner_id, team_ids=team_ids, is_admin=is_admin)
        with self._get_conn() as conn:
            sql = (
                f"SELECT *, (SELECT SUM(cost) FROM llm_usage WHERE job_id = jobs.id) as cost, "
                f"(SELECT SUM(input_tokens + output_tokens) FROM llm_usage WHERE job_id = jobs.id) as tokens "
                f"FROM jobs{where} ORDER BY created_at DESC"
            )
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_dict(row) for row in rows]

    _SORTABLE_COLUMNS = {"created_at", "vision", "status", "progress", "current_phase"}

    def _build_where(
        self,
        vision_filter: Optional[str] = None,
        status_filter: Optional[str] = None,
        owner_id: Optional[str] = None,
        team_ids: Optional[List[str]] = None,
        is_admin: bool = False,
        team_id: Optional[str] = None,
    ) -> tuple:
        """Build WHERE clause and params from optional filters and access restrictions."""
        clauses: list = []
        params: list = []
        if vision_filter:
            clauses.append("vision LIKE ?")
            params.append(f"%{vision_filter}%")
        if status_filter:
            clauses.append("status = ?")
            params.append(status_filter)
            
        if not is_admin:
            access_clauses = []
            if owner_id:
                if team_id == "personal":
                    access_clauses.append("owner_id = ? AND (team_id IS NULL OR team_id = '')")
                    params.append(owner_id)
                elif team_id:
                    allowed_teams = team_ids or []
                    if team_id in allowed_teams:
                        access_clauses.append("team_id = ?")
                        params.append(team_id)
                    else:
                        # User has no access to this team! Force empty results.
                        access_clauses.append("1 = 0")
                else:
                    access_clauses.append("owner_id = ?")
                    params.append(owner_id)
                    if team_ids:
                        team_placeholders = ", ".join("?" for _ in team_ids)
                        access_clauses.append(f"team_id IN ({team_placeholders})")
                        params.extend(team_ids)
            
            if access_clauses:
                if len(access_clauses) > 1 and not team_id:
                    clauses.append(f"({' OR '.join(access_clauses)})")
                else:
                    clauses.append(access_clauses[0])
            else:
                # Force empty results for non-admins with no owner or team parameters
                clauses.append("1 = 0")
        else:
            # Admin filters
            if team_id == "personal":
                clauses.append("(team_id IS NULL OR team_id = '')")
            elif team_id:
                clauses.append("team_id = ?")
                params.append(team_id)
                
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        return where, params

    def get_jobs_count(
        self,
        vision_filter: Optional[str] = None,
        status_filter: Optional[str] = None,
        owner_id: Optional[str] = None,
        team_ids: Optional[List[str]] = None,
        is_admin: bool = False,
        team_id: Optional[str] = None,
    ) -> int:
        """Return total number of jobs, optionally filtered and scoped by access."""
        where, params = self._build_where(
            vision_filter, status_filter, owner_id=owner_id, team_ids=team_ids, is_admin=is_admin, team_id=team_id
        )
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
        owner_id: Optional[str] = None,
        team_ids: Optional[List[str]] = None,
        is_admin: bool = False,
        team_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get jobs with pagination, optional filters, sorting, and access control."""
        where, params = self._build_where(
            vision_filter, status_filter, owner_id=owner_id, team_ids=team_ids, is_admin=is_admin, team_id=team_id
        )

        col = sort_by if sort_by in self._SORTABLE_COLUMNS else "created_at"
        direction = "ASC" if sort_order == "asc" else "DESC"
        collate = " COLLATE NOCASE" if col == "vision" else ""

        sql = (
            f"SELECT *, (SELECT SUM(cost) FROM llm_usage WHERE job_id = jobs.id) as cost, "
            f"(SELECT SUM(input_tokens + output_tokens) FROM llm_usage WHERE job_id = jobs.id) as tokens "
            f"FROM jobs{where} ORDER BY {col}{collate} {direction} LIMIT ? OFFSET ?"
        )
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

    def add_skills_used(self, job_id: str, skills: List[str]) -> bool:
        """Append skills to the job's metadata."""
        if not skills:
            return False
        with self._get_conn() as conn:
            row = conn.execute("SELECT metadata FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not row:
                return False
            try:
                metadata = json.loads(row["metadata"] or "{}")
            except Exception:
                metadata = {}
            
            existing_skills = set(metadata.get("skills_used", []))
            new_skills = existing_skills.union(skills)
            
            if len(new_skills) == len(existing_skills):
                return True
                
            metadata["skills_used"] = sorted(list(new_skills))
            conn.execute("UPDATE jobs SET metadata = ? WHERE id = ?", (json.dumps(metadata), job_id))
            return True

    def delete_job(self, job_id: str) -> bool:
        """Delete a job and its related records. Returns True if job was found and deleted."""
        with self._get_conn() as conn:
            conn.execute("DELETE FROM refinements WHERE job_id = ?", (job_id,))
            conn.execute("DELETE FROM documents WHERE job_id = ?", (job_id,))
            conn.execute("DELETE FROM migration_issues WHERE job_id = ?", (job_id,))
            conn.execute("DELETE FROM validation_issues WHERE job_id = ?", (job_id,))
            cursor = conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            return cursor.rowcount > 0

    _TERMINAL_STATUSES = frozenset({"completed", "partially_completed", "failed", "cancelled"})

    def update_progress(self, job_id: str, phase: str, progress: int, message: str = None):
        """Update job progress and optionally append a message.

        Refuses to overwrite a job that has already reached a terminal
        status (completed, failed, cancelled) — prevents late progress
        writes from reverting final state.
        """
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT status, last_message FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if not row:
                return False
            if row["status"] in self._TERMINAL_STATUSES:
                return False

            updates: Dict[str, Any] = {
                "current_phase": phase,
                "progress": progress,
            }

            if message:
                try:
                    messages = json.loads(row["last_message"] or "[]")
                except (json.JSONDecodeError, TypeError):
                    messages = []
                messages.append({
                    "timestamp": datetime.now().isoformat(),
                    "phase": phase,
                    "message": message,
                })
                messages = messages[-50:]
                updates["last_message"] = json.dumps(messages)

            set_clause = ", ".join(f"{k} = ?" for k in updates)
            values = list(updates.values()) + [job_id]
            conn.execute(
                f"UPDATE jobs SET {set_clause} WHERE id = ? AND status NOT IN ({','.join('?' for _ in self._TERMINAL_STATUSES)})",
                values + list(self._TERMINAL_STATUSES),
            )
            return True
    
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
    
    def mark_partially_completed(
        self, job_id: str, warning: str, results: Optional[Dict[str, Any]] = None
    ):
        """Mark job as partially completed — all phases ran but validation issues remain.

        Unlike ``mark_failed`` this signals that usable code was generated and
        the job reached completion, but with known quality issues.
        """
        updates: Dict[str, Any] = {
            'status': 'partially_completed',
            'progress': 100,
            'current_phase': 'completed',
            'completed_at': datetime.now().isoformat(),
            'error': warning,
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
    
    def get_stats(self, owner_id: Optional[str] = None,
                  team_ids: Optional[List[str]] = None,
                  is_admin: bool = False) -> Dict[str, Any]:
        """Get aggregate statistics across all jobs, scoped by access."""
        where, params = self._build_where(owner_id=owner_id, team_ids=team_ids, is_admin=is_admin)
        with self._get_conn() as conn:
            cursor = conn.execute(f"""
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN status IN ('completed', 'partially_completed') THEN 1 ELSE 0 END) as completed,
                    SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) as running,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed,
                    SUM(CASE WHEN status = 'quota_exhausted' THEN 1 ELSE 0 END) as quota_exhausted,
                    SUM(CASE WHEN status = 'queued' THEN 1 ELSE 0 END) as queued,
                    SUM((SELECT SUM(cost) FROM llm_usage WHERE job_id = jobs.id)) as total_cost,
                    SUM((SELECT SUM(input_tokens + output_tokens) FROM llm_usage WHERE job_id = jobs.id)) as total_tokens
                FROM jobs
                {where}
            """, params)
            row = cursor.fetchone()
            return {
                'total_jobs': row['total'] or 0,
                'completed': row['completed'] or 0,
                'running': row['running'] or 0,
                'failed': row['failed'] or 0,
                'quota_exhausted': row['quota_exhausted'] or 0,
                'queued': row['queued'] or 0,
                'total_cost': row['total_cost'] or 0.0,
                'total_tokens': row['total_tokens'] or 0
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

    # ── LLM Usage ────────────────────────────────────────────────────────────

    def record_llm_usage(self, job_id: str, agent_name: str, model: str, input_tokens: int, output_tokens: int, cost: float) -> str:
        """Record LLM token usage and cost for a job."""
        import uuid
        now = datetime.now().isoformat()
        usage_id = str(uuid.uuid4())
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO llm_usage (id, job_id, agent_name, model, input_tokens, output_tokens, cost, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (usage_id, job_id, agent_name, model, input_tokens, output_tokens, cost, now))
        return usage_id

    def get_llm_usage(self, job_id: str) -> List[Dict[str, Any]]:
        """Get all LLM usage records for a job."""
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM llm_usage WHERE job_id = ? ORDER BY created_at", (job_id,)).fetchall()
            return [dict(r) for r in rows]

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


    # ── Validation Issues ───────────────────────────────────────────

    def create_validation_issue(
        self,
        issue_id: str,
        job_id: str,
        check_name: str,
        severity: str,
        file_path: Optional[str],
        line_number: Optional[int],
        description: str,
    ) -> Dict[str, Any]:
        """Create a validation issue record (status=pending)."""
        now = datetime.now().isoformat()
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO validation_issues
                    (id, job_id, check_name, severity, file_path, line_number,
                     description, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """, (issue_id, job_id, check_name, severity, file_path,
                  line_number, description, now))
        return {
            'id': issue_id,
            'job_id': job_id,
            'check_name': check_name,
            'severity': severity,
            'file_path': file_path,
            'line_number': line_number,
            'description': description,
            'fix_strategy': None,
            'status': 'pending',
            'error': None,
            'created_at': now,
            'completed_at': None,
        }

    def get_validation_issues(
        self, job_id: str, check_name: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return validation issues for a job, optionally filtered."""
        clauses = ["job_id = ?"]
        params: list = [job_id]
        if check_name:
            clauses.append("check_name = ?")
            params.append(check_name)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = " AND ".join(clauses)
        with self._get_conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM validation_issues WHERE {where} ORDER BY created_at",
                params,
            ).fetchall()
            return [dict(r) for r in rows]

    def update_validation_issue_status(
        self, issue_id: str, status: str,
        error: Optional[str] = None,
        fix_strategy: Optional[str] = None,
    ) -> bool:
        """Update a validation issue's status. Sets completed_at on terminal states."""
        updates: Dict[str, Any] = {'status': status}
        if error is not None:
            updates['error'] = error
        if fix_strategy is not None:
            updates['fix_strategy'] = fix_strategy
        if status in ('completed', 'failed'):
            updates['completed_at'] = datetime.now().isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [issue_id]
        with self._get_conn() as conn:
            cursor = conn.execute(
                f"UPDATE validation_issues SET {set_clause} WHERE id = ?", values
            )
            return cursor.rowcount > 0

    def get_pending_validation_issues(self, job_id: str) -> List[Dict[str, Any]]:
        """Return validation issues with status='pending'."""
        return self.get_validation_issues(job_id, status='pending')

    def get_failed_validation_issues(self, job_id: str) -> List[Dict[str, Any]]:
        """Return validation issues with status='failed'."""
        return self.get_validation_issues(job_id, status='failed')

    def delete_validation_issues(self, job_id: str) -> int:
        """Delete ALL validation issues for a job (used before clean re-runs)."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM validation_issues WHERE job_id = ?",
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
        
        if job.get('metadata'):
            try:
                job['metadata'] = json.loads(job['metadata'])
            except (json.JSONDecodeError, TypeError):
                job['metadata'] = {}
        else:
            job['metadata'] = {}
        
        return job

    # ------------------------------------------------------------------
    # Jira configuration — encrypted per-user storage
    # ------------------------------------------------------------------

    _JIRA_SECRET_KEY = "jira_config_secret"

    def _get_fernet(self):
        """Return a Fernet cipher for encrypting Jira API tokens.

        Priority:
          1. JIRA_CONFIG_SECRET env var (explicit override / CI / Kubernetes secret)
          2. Key stored in the system_config table (auto-generated on first use)
          3. Generate a new key, persist it, and use it going forward

        The auto-generated key is stored in the database so all future starts
        use the same key without any manual configuration.
        """
        from cryptography.fernet import Fernet
        raw = os.getenv("JIRA_CONFIG_SECRET", "").strip()
        if raw:
            return Fernet(raw.encode() if isinstance(raw, str) else raw)

        # Look up persisted key
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM system_config WHERE key = ?",
                (self._JIRA_SECRET_KEY,)
            ).fetchone()
            if row:
                return Fernet(row["value"].encode())

            # First time — generate, persist, and use
            key = Fernet.generate_key()
            conn.execute(
                "INSERT INTO system_config (key, value, created_at) VALUES (?, ?, ?)",
                (self._JIRA_SECRET_KEY, key.decode(), datetime.now().isoformat())
            )
        import logging
        logging.getLogger(__name__).info(
            "Generated new Jira encryption key and saved to system_config table. "
            "Export JIRA_CONFIG_SECRET from the DB if you need to migrate the database."
        )
        return Fernet(key)

    def save_jira_config(self, owner_id: str, jira_base_url: str,
                         jira_email: str, api_token: str) -> None:
        """Encrypt and persist Jira credentials for owner_id."""
        if not owner_id:
            raise ValueError("owner_id is required to save Jira config")
        f = self._get_fernet()
        encrypted = f.encrypt(api_token.encode()).decode()
        now = datetime.now().isoformat()
        with self._get_conn() as conn:
            # Remove legacy rows saved before auth supplied a stable owner_id
            conn.execute("DELETE FROM jira_configs WHERE owner_id IS NULL")
            conn.execute("""
                INSERT INTO jira_configs (owner_id, jira_base_url, jira_email, encrypted_token, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(owner_id) DO UPDATE SET
                    jira_base_url = excluded.jira_base_url,
                    jira_email = excluded.jira_email,
                    encrypted_token = excluded.encrypted_token,
                    updated_at = excluded.updated_at
            """, (owner_id, jira_base_url, jira_email, encrypted, now))

    def get_jira_config(self, owner_id: str) -> Optional[Dict[str, str]]:
        """Return Jira config for owner_id with the token decrypted, or None."""
        if not owner_id:
            return None
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT jira_base_url, jira_email, encrypted_token, updated_at "
                "FROM jira_configs WHERE owner_id = ?",
                (owner_id,)
            ).fetchone()
            if not row:
                # Migrate a legacy NULL-owner row (saved before sub claim was wired)
                legacy = conn.execute(
                    "SELECT rowid, jira_base_url, jira_email, encrypted_token, updated_at "
                    "FROM jira_configs WHERE owner_id IS NULL "
                    "ORDER BY updated_at DESC LIMIT 1"
                ).fetchone()
                if legacy:
                    conn.execute(
                        "UPDATE jira_configs SET owner_id = ? WHERE rowid = ?",
                        (owner_id, legacy["rowid"]),
                    )
                    conn.execute("DELETE FROM jira_configs WHERE owner_id IS NULL")
                    row = legacy
        if not row:
            return None
        f = self._get_fernet()
        try:
            token = f.decrypt(row["encrypted_token"].encode()).decode()
        except Exception:
            token = ""
        return {
            "jira_base_url": row["jira_base_url"],
            "jira_email": row["jira_email"],
            "api_token": token,
            "updated_at": row["updated_at"],
        }

    def delete_jira_config(self, owner_id: str) -> bool:
        """Remove stored Jira config for owner_id. Returns True if a row was deleted."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM jira_configs WHERE owner_id = ?", (owner_id,)
            )
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # GitHub configuration — encrypted per-user storage
    # ------------------------------------------------------------------

    def save_github_config(self, owner_id: str, token: str,
                           github_username: str = "") -> None:
        """Encrypt and persist GitHub PAT for owner_id."""
        f = self._get_fernet()
        encrypted = f.encrypt(token.encode()).decode()
        now = datetime.now().isoformat()
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO github_configs (owner_id, github_username, encrypted_token, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(owner_id) DO UPDATE SET
                    github_username = excluded.github_username,
                    encrypted_token = excluded.encrypted_token,
                    updated_at = excluded.updated_at
            """, (owner_id, github_username, encrypted, now))

    def get_github_config(self, owner_id: str) -> Optional[Dict[str, Any]]:
        """Return GitHub config for owner_id (token decrypted). None if not configured."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM github_configs WHERE owner_id = ?", (owner_id,)
            ).fetchone()
        if not row:
            return None
        f = self._get_fernet()
        return {
            "owner_id": row["owner_id"],
            "github_username": row["github_username"] or "",
            "token": f.decrypt(row["encrypted_token"].encode()).decode(),
            "updated_at": row["updated_at"],
        }

    def delete_github_config(self, owner_id: str) -> bool:
        """Remove stored GitHub config for owner_id."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM github_configs WHERE owner_id = ?", (owner_id,)
            )
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # LLM configuration — encrypted per-user storage
    # ------------------------------------------------------------------

    def save_llm_config(self, owner_id: str, api_base_url: str,
                        api_key: str, model_manager: str = "gpt-4o-mini",
                        model_worker: str = "gpt-4o-mini",
                        model_reviewer: str = "gpt-4o-mini") -> None:
        """Encrypt and persist LLM credentials for owner_id."""
        f = self._get_fernet()
        encrypted = f.encrypt(api_key.encode()).decode()
        now = datetime.now().isoformat()
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO user_llm_configs (owner_id, api_base_url, encrypted_key, 
                                            model_manager, model_worker, model_reviewer, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(owner_id) DO UPDATE SET
                    api_base_url = excluded.api_base_url,
                    encrypted_key = excluded.encrypted_key,
                    model_manager = excluded.model_manager,
                    model_worker = excluded.model_worker,
                    model_reviewer = excluded.model_reviewer,
                    updated_at = excluded.updated_at
            """, (owner_id, api_base_url, encrypted, model_manager, model_worker, model_reviewer, now))

    def get_llm_config(self, owner_id: str) -> Optional[Dict[str, str]]:
        """Return LLM config for owner_id with the API key decrypted, or None."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT api_base_url, encrypted_key, model_manager, model_worker, model_reviewer, updated_at "
                "FROM user_llm_configs WHERE owner_id = ?",
                (owner_id,)
            ).fetchone()
        if not row:
            return None
        f = self._get_fernet()
        try:
            api_key = f.decrypt(row["encrypted_key"].encode()).decode()
        except Exception:
            api_key = ""
        return {
            "api_base_url": row["api_base_url"],
            "api_key": api_key,
            "model_manager": row["model_manager"],
            "model_worker": row["model_worker"],
            "model_reviewer": row["model_reviewer"],
            "updated_at": row["updated_at"],
        }

    def delete_llm_config(self, owner_id: str) -> bool:
        """Remove stored LLM config for owner_id. Returns True if a row was deleted."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM user_llm_configs WHERE owner_id = ?", (owner_id,)
            )
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Model context windows dictionary (stores model token limits)
    # ------------------------------------------------------------------

    def get_model_context_window(self, model_name: str) -> Optional[int]:
        """Query the model_context_windows table for a matching pattern using LIKE."""
        if not model_name:
            return None
        with self._get_conn() as conn:
            row = conn.execute("""
                SELECT context_window 
                FROM model_context_windows 
                WHERE ? LIKE '%' || model_pattern || '%' 
                ORDER BY length(model_pattern) DESC 
                LIMIT 1
            """, (model_name.lower(),)).fetchone()
            return row["context_window"] if row else None

    def save_model_context_window(self, model_pattern: str, context_window: int) -> None:
        """Upsert a model pattern and its context window size."""
        now = datetime.now().isoformat()
        with self._get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO model_context_windows (model_pattern, context_window, updated_at)
                VALUES (?, ?, ?)
            """, (model_pattern.lower(), context_window, now))

    def delete_model_context_window(self, model_pattern: str) -> bool:
        """Delete a model pattern entry. Returns True if a row was deleted."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM model_context_windows WHERE model_pattern = ?",
                (model_pattern.lower(),)
            )
            return cursor.rowcount > 0

    def get_all_model_context_windows(self) -> List[Dict[str, Any]]:
        """Return all model patterns and their context windows."""
        with self._get_conn() as conn:
            cursor = conn.execute("SELECT model_pattern, context_window FROM model_context_windows ORDER BY model_pattern ASC")
            return [{"model_pattern": row["model_pattern"], "context_window": row["context_window"]} for row in cursor.fetchall()]

