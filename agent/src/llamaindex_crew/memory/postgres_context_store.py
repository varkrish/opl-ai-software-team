"""
Postgres storage layer for the context plane (crew_context database).

Persists job records, check-level validation outcomes, prompts (relational, un-embedded),
structured artifacts (jsonb intact), call-graph edges, and prose vector embeddings.

DSN resolved from CREW_DOC_INDEX_DSN environment variable.
Fail-open: if DB is unreachable, logs loudly with logger.error and returns empty results.
No local disk fallback.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

# Fallback DSN for local development outside containers if CREW_DOC_INDEX_DSN is unset
DEFAULT_LOCAL_DSN = "postgresql://memmachine:memmachine_password@127.0.0.1:55432/crew_context"

try:
    import psycopg
    HAS_PSYCOPG = True
except ImportError:
    psycopg = None
    HAS_PSYCOPG = False


def resolve_dsn() -> str:
    raw = os.getenv("CREW_DOC_INDEX_DSN") or DEFAULT_LOCAL_DSN
    # psycopg expects postgresql:// or postgres:// prefix
    if raw.startswith("postgresql+psycopg://"):
        return "postgresql://" + raw[len("postgresql+psycopg://"):]
    if raw.startswith("postgresql+psycopg2://"):
        return "postgresql://" + raw[len("postgresql+psycopg2://"):]
    return raw


@dataclass
class ScopedJobRecord:
    job_id: str
    scope_org: str
    scope_project: str
    scope_domain: str
    vision: str
    tech_stack: str
    capability_profile: str
    status: str
    created_at: str


class PostgresContextStore:
    """Postgres context plane store for crew_context database."""

    def __init__(self, dsn: Optional[str] = None):
        self.dsn = dsn or resolve_dsn()
        self._schema_initialized = False

    def _get_connection(self) -> Any:
        if not HAS_PSYCOPG:
            logger.error("psycopg driver is not installed; context memory plane is unreachable")
            return None

        try:
            # Try connecting directly to the specified DSN
            conn = psycopg.connect(self.dsn, connect_timeout=5)
            return conn
        except Exception as exc:
            # If crew_context database does not exist yet, connect to postgres/memmachine db to create it
            try:
                if "database" in str(exc).lower() or "does not exist" in str(exc).lower():
                    base_dsn = self.dsn.rsplit("/", 1)[0] + "/postgres"
                    with psycopg.connect(base_dsn, autocommit=True, connect_timeout=5) as sys_conn:
                        with sys_conn.cursor() as cur:
                            cur.execute("CREATE DATABASE crew_context;")
                    return psycopg.connect(self.dsn, connect_timeout=5)
            except Exception as create_exc:
                logger.error("Failed to auto-create crew_context database: %s", create_exc)

            logger.error("Postgres context plane unreachable at %s: %s", self.dsn, exc)
            return None

    def init_schema(self) -> bool:
        if self._schema_initialized:
            return True

        conn = self._get_connection()
        if not conn:
            return False

        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS jobs (
                            job_id TEXT PRIMARY KEY,
                            scope_org TEXT NOT NULL,
                            scope_project TEXT NOT NULL,
                            scope_domain TEXT NOT NULL,
                            vision TEXT,
                            tech_stack TEXT,
                            capability_profile TEXT,
                            status TEXT,
                            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                        );
                    """)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS job_outcomes (
                            id BIGSERIAL PRIMARY KEY,
                            job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
                            check_name TEXT NOT NULL,
                            severity TEXT DEFAULT 'error',
                            passed BOOLEAN NOT NULL DEFAULT TRUE,
                            file_path TEXT,
                            description TEXT,
                            iterations INT DEFAULT 0,
                            converged BOOLEAN DEFAULT TRUE,
                            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                        );
                    """)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS prompts (
                            id BIGSERIAL PRIMARY KEY,
                            job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
                            doc_type TEXT NOT NULL,
                            prompt_text TEXT NOT NULL,
                            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                        );
                    """)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS artifacts (
                            id BIGSERIAL PRIMARY KEY,
                            job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
                            doc_type TEXT NOT NULL,
                            content_json JSONB,
                            content_text TEXT,
                            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                        );
                    """)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS prose_vectors (
                            id BIGSERIAL PRIMARY KEY,
                            job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
                            doc_type TEXT NOT NULL,
                            text TEXT NOT NULL,
                            embedding vector(384),
                            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                        );
                    """)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS call_graph_edges (
                            id BIGSERIAL PRIMARY KEY,
                            job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
                            from_file TEXT NOT NULL,
                            from_func TEXT NOT NULL,
                            to_file TEXT NOT NULL,
                            to_func TEXT NOT NULL,
                            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                        );
                    """)
                    cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_scope ON jobs(scope_org, scope_project, scope_domain);")
                    cur.execute("CREATE INDEX IF NOT EXISTS idx_outcomes_job ON job_outcomes(job_id, check_name);")
                    cur.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_job ON artifacts(job_id, doc_type);")
                    cur.execute("CREATE INDEX IF NOT EXISTS idx_edges_job ON call_graph_edges(job_id);")

            self._schema_initialized = True
            logger.info("Initialized crew_context database schema successfully")
            return True
        except Exception as exc:
            logger.error("Failed initializing crew_context schema: %s", exc)
            return False
        finally:
            conn.close()

    def record_job(
        self,
        job_id: str,
        scope_org: str,
        scope_project: str,
        scope_domain: str,
        vision: str,
        tech_stack: str = "",
        capability_profile: str = "",
        status: str = "completed",
        outcomes: Optional[List[Dict[str, Any]]] = None,
        prompts: Optional[List[Dict[str, str]]] = None,
        json_artifacts: Optional[Dict[str, Any]] = None,
        prose_documents: Optional[List[Dict[str, str]]] = None,
        call_graph_edges: Optional[List[Dict[str, str]]] = None,
        embeddings_map: Optional[Dict[str, List[float]]] = None,
    ) -> bool:
        if not self.init_schema():
            return False

        conn = self._get_connection()
        if not conn:
            return False

        try:
            with conn:
                with conn.cursor() as cur:
                    now = datetime.now(timezone.utc)
                    # 1. Upsert Job Record
                    cur.execute("""
                        INSERT INTO jobs (job_id, scope_org, scope_project, scope_domain, vision, tech_stack, capability_profile, status, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (job_id) DO UPDATE SET
                            scope_org = EXCLUDED.scope_org,
                            scope_project = EXCLUDED.scope_project,
                            scope_domain = EXCLUDED.scope_domain,
                            vision = EXCLUDED.vision,
                            tech_stack = EXCLUDED.tech_stack,
                            capability_profile = EXCLUDED.capability_profile,
                            status = EXCLUDED.status;
                    """, (job_id, scope_org, scope_project, scope_domain, vision, tech_stack, capability_profile, status, now))

                    # 2. Record Outcomes
                    if outcomes is not None:
                        cur.execute("DELETE FROM job_outcomes WHERE job_id = %s;", (job_id,))
                        for item in outcomes:
                            cur.execute("""
                                INSERT INTO job_outcomes (job_id, check_name, severity, passed, file_path, description, iterations, converged, created_at)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
                            """, (
                                job_id,
                                item.get("check_name", "general"),
                                item.get("severity", "error"),
                                bool(item.get("passed", True)),
                                item.get("file_path"),
                                item.get("description", ""),
                                int(item.get("iterations", 0)),
                                bool(item.get("converged", True)),
                                now,
                            ))

                    # 3. Record Prompts
                    if prompts:
                        cur.execute("DELETE FROM prompts WHERE job_id = %s;", (job_id,))
                        for p in prompts:
                            cur.execute("""
                                INSERT INTO prompts (job_id, doc_type, prompt_text, created_at)
                                VALUES (%s, %s, %s, %s);
                            """, (job_id, p.get("doc_type", "unknown"), p.get("prompt_text", ""), now))

                    # 4. Record JSONB Artifacts
                    if json_artifacts:
                        for doc_type, content in json_artifacts.items():
                            cur.execute("DELETE FROM artifacts WHERE job_id = %s AND doc_type = %s;", (job_id, doc_type))
                            content_json = json.dumps(content) if isinstance(content, (dict, list)) else content
                            cur.execute("""
                                INSERT INTO artifacts (job_id, doc_type, content_json, created_at)
                                VALUES (%s, %s, %s::jsonb, %s);
                            """, (job_id, doc_type, content_json, now))

                    # 5. Record Prose Documents & Vectors
                    if prose_documents:
                        for pd in prose_documents:
                            doc_type = pd.get("doc_type", "prose")
                            text = pd.get("text", "")
                            cur.execute("DELETE FROM prose_vectors WHERE job_id = %s AND doc_type = %s;", (job_id, doc_type))
                            vec = embeddings_map.get(text) if embeddings_map else None
                            vec_str = str(vec) if vec else None
                            cur.execute("""
                                INSERT INTO prose_vectors (job_id, doc_type, text, embedding, created_at)
                                VALUES (%s, %s, %s, %s::vector, %s);
                            """, (job_id, doc_type, text, vec_str, now))

                    # 6. Record Call Graph Edges
                    if call_graph_edges:
                        cur.execute("DELETE FROM call_graph_edges WHERE job_id = %s;", (job_id,))
                        for edge in call_graph_edges:
                            cur.execute("""
                                INSERT INTO call_graph_edges (job_id, from_file, from_func, to_file, to_func, created_at)
                                VALUES (%s, %s, %s, %s, %s, %s);
                            """, (
                                job_id,
                                edge.get("from_file", ""),
                                edge.get("from_func", ""),
                                edge.get("to_file", ""),
                                edge.get("to_func", ""),
                                now,
                            ))

            logger.info("Successfully recorded job %s into Postgres crew_context", job_id)
            return True
        except Exception as exc:
            logger.error("Failed recording job %s into Postgres context plane: %s", job_id, exc)
            return False
        finally:
            conn.close()

    def get_job_outcomes(self, job_id: str) -> List[Dict[str, Any]]:
        conn = self._get_connection()
        if not conn:
            return []
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT check_name, severity, passed, file_path, description, iterations, converged
                    FROM job_outcomes WHERE job_id = %s;
                """, (job_id,))
                rows = cur.fetchall()
                return [
                    {
                        "check_name": r[0],
                        "severity": r[1],
                        "passed": r[2],
                        "file_path": r[3],
                        "description": r[4],
                        "iterations": r[5],
                        "converged": r[6],
                    }
                    for r in rows
                ]
        except Exception as exc:
            logger.error("Failed fetching outcomes for job %s: %s", job_id, exc)
            return []
        finally:
            conn.close()

    def get_artifact(self, job_id: str, doc_type: str) -> Optional[Union[Dict[str, Any], str]]:
        conn = self._get_connection()
        if not conn:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT content_json, content_text FROM artifacts WHERE job_id = %s AND doc_type = %s LIMIT 1;", (job_id, doc_type))
                row = cur.fetchone()
                if not row:
                    return None
                content_json, content_text = row
                if content_json is not None:
                    return content_json
                return content_text
        except Exception as exc:
            logger.error("Failed fetching artifact %s for job %s: %s", doc_type, job_id, exc)
            return None
        finally:
            conn.close()

    def get_passed_jobs_in_scope(
        self,
        org_id: str,
        project_id: str,
        domain: str,
        required_checks: Optional[List[str]] = None,
    ) -> List[str]:
        """Find job_ids in scope where required validation checks passed."""
        conn = self._get_connection()
        if not conn:
            return []
        try:
            with conn.cursor() as cur:
                # Any job that reached a terminal state is a candidate. Gating on
                # status = 'completed' here would discard most of the corpus
                # before the per-check logic ever runs: in the live database
                # partially_completed outnumbers completed 32 to 9, and job
                # 107b3d3e was partially_completed with five failing checks but
                # ZERO wiring or package issues — its contract is reusable even
                # though the job as a whole was not clean. Judging per check is
                # the entire point of storing outcomes per check.
                cur.execute("""
                    SELECT job_id FROM jobs
                    WHERE scope_org = %s AND scope_project = %s AND scope_domain = %s
                    AND status IN ('completed', 'partially_completed', 'completed_with_errors');
                """, (org_id, project_id, domain))
                job_ids = [r[0] for r in cur.fetchall()]

                if not job_ids:
                    return []

                passed_jobs = []
                for jid in job_ids:
                    outcomes = self.get_job_outcomes(jid)
                    if not outcomes:
                        # No recorded outcomes means UNKNOWN, not good. Handing
                        # back an unverified blueprint is the failure this whole
                        # design exists to prevent — critique_score 9 on a
                        # contract containing only tests. Fail closed.
                        logger.debug(
                            "Skipping job %s as a blueprint source: no recorded outcomes", jid
                        )
                        continue

                    recorded = {out["check_name"] for out in outcomes}
                    failed_checks = {out["check_name"] for out in outcomes if not out["passed"]}

                    if required_checks is None:
                        # No specific ask: require a clean sweep of what was recorded.
                        if not failed_checks:
                            passed_jobs.append(jid)
                        continue

                    # Every required check must have been recorded AND passed.
                    # Absent evidence is not evidence of passing.
                    if all(c in recorded and c not in failed_checks for c in required_checks):
                        passed_jobs.append(jid)

                return passed_jobs
        except Exception as exc:
            logger.error("Failed getting passed jobs in scope: %s", exc)
            return []
        finally:
            conn.close()

    def get_call_graph_edges(self, job_id: str) -> List[Dict[str, str]]:
        conn = self._get_connection()
        if not conn:
            return []
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT from_file, from_func, to_file, to_func FROM call_graph_edges WHERE job_id = %s;", (job_id,))
                return [
                    {"from_file": r[0], "from_func": r[1], "to_file": r[2], "to_func": r[3]}
                    for r in cur.fetchall()
                ]
        except Exception as exc:
            logger.error("Failed getting call graph edges for job %s: %s", job_id, exc)
            return []
        finally:
            conn.close()



def _outcomes_from_validation_report(workspace_path: Optional[Path]) -> List[Dict[str, Any]]:
    """Every check the validator ran, with its verdict, from validation_report.json.

    Returns [] when there is no readable report, so the caller can fall back.
    """
    if not workspace_path:
        return []
    report_file = Path(workspace_path) / "validation_report.json"
    if not report_file.is_file():
        return []
    try:
        checks = (json.loads(report_file.read_text(encoding="utf-8")) or {}).get("checks")
    except Exception as exc:  # noqa: BLE001 — a bad report must not fail the write
        logger.warning("Could not read %s: %s", report_file, exc)
        return []
    if not isinstance(checks, dict):
        return []

    outcomes: List[Dict[str, Any]] = []
    for name, detail in checks.items():
        if not isinstance(detail, dict):
            continue
        if detail.get("skipped"):
            # A skipped check is not evidence either way; recording it as passed
            # would qualify a blueprint on a check nobody ran.
            continue
        passed = bool(detail.get("pass"))
        outcomes.append({
            "check_name": str(name),
            "severity": "error" if not passed else "info",
            "passed": passed,
            "file_path": None,
            "description": "" if passed else str(detail)[:500],
        })
    return outcomes


def sync_job_from_sqlite(
    sqlite_db_path: Union[str, Path],
    job_id: str,
    scope_org: str,
    scope_project: str,
    scope_domain: str,
    store: Optional[PostgresContextStore] = None,
    workspace_path: Optional[Path] = None,
) -> bool:
    """Source the job record and its per-check outcomes into the context plane.

    Outcomes come from ``validation_report.json`` in the workspace when it is
    available, and fall back to the SQLite ``validation_issues`` table.

    That order matters. ``validation_issues`` is a FAILURES table — it holds one
    row per problem found and nothing at all for a check that passed. On job
    107b3d3e it carried 5 rows while the report recorded 15 checks, 12 of them
    passing. Sourcing outcomes from it alone means a passing check is
    indistinguishable from a check that never ran, and since a required check
    must be recorded AND passed to qualify a blueprint, no job could ever be
    reusable. The whole seeding path was inert.

    The report is the ground truth: report["checks"][name]["pass"] exists for
    every check that ran."""
    db_path = Path(sqlite_db_path)
    if not db_path.is_file():
        return False

    try:
        conn = sqlite3.connect(str(db_path))
        c = conn.cursor()
        c.execute("SELECT vision, status, results, error, metadata FROM jobs WHERE id = ?;", (job_id,))
        job_row = c.fetchone()
        if not job_row:
            conn.close()
            return False

        vision, status, results_json, error_msg, meta_json = job_row
        c.execute("SELECT check_name, severity, status, file_path, description FROM validation_issues WHERE job_id = ?;", (job_id,))
        issue_rows = c.fetchall()
        conn.close()

        outcomes = _outcomes_from_validation_report(workspace_path)
        if not outcomes:
            # No report on disk (older job, or it never reached validation).
            # Fall back to the failures table, accepting that it can only ever
            # describe what went wrong.
            for check_name, severity, issue_status, file_path, desc in issue_rows:
                passed = issue_status != "pending" and severity != "error"
                outcomes.append({
                    "check_name": check_name,
                    "severity": severity,
                    "passed": passed,
                    "file_path": file_path,
                    "description": desc,
                })

        store_inst = store or PostgresContextStore()
        return store_inst.record_job(
            job_id=job_id,
            scope_org=scope_org,
            scope_project=scope_project,
            scope_domain=scope_domain,
            vision=vision or "",
            status=status or "completed",
            outcomes=outcomes,
        )
    except Exception as exc:
        logger.error("Failed syncing job %s from SQLite: %s", job_id, exc)
        return False
