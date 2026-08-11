"""
Unit tests for context memory agent tools (Stage 4).
Verifies:
1. Every return carries its outcome.
2. Whole intact artifacts are returned.
3. Reference implementations fail closed.
4. check_known_bad catches anti-patterns (tests-only contract, bad dependency, route mismatch).
"""
import uuid

import pytest
from llamaindex_crew.memory.postgres_context_store import PostgresContextStore
from llamaindex_crew.tools.context_tools import (
    find_similar_solutions,
    get_prior_artifact,
    find_reference_implementation,
    find_fix_precedent,
    check_known_bad,
)


@pytest.fixture
def store():
    ps = PostgresContextStore()
    if not ps.init_schema():
        pytest.skip("Postgres crew_context DB unavailable at 127.0.0.1:55432")
    return ps


def test_context_tools_outcomes_and_anti_pattern_check(store):
    job_id = "test-job-stage4-" + uuid.uuid4().hex[:8]
    store.record_job(
        job_id=job_id,
        scope_org="testorg",
        scope_project="testproj",
        scope_domain="web",
        vision="Build Express and React fullstack dashboard",
        outcomes=[
            {"check_name": "wiring_reconciliation", "passed": True},
            {"check_name": "client_server_contract", "passed": False, "severity": "error", "description": "Route mismatch: /api/v1/stream vs /events"},
        ],
        json_artifacts={
            "wiring_contract": {"language": "typescript", "packages": [{"name": "express"}, {"name": "react"}]},
        },
    )

    # 1. find_similar_solutions carries outcome
    solutions = find_similar_solutions("dashboard", limit=2)
    assert job_id in solutions
    assert "FAILED CHECKS: client_server_contract" in solutions

    # 2. get_prior_artifact returns intact artifact with outcome header
    artifact_res = get_prior_artifact(job_id, "wiring_contract")
    assert "express" in artifact_res
    assert "FAILED CHECKS" in artifact_res

    # 3. find_reference_implementation fails closed for job with failing check
    ref_res = find_reference_implementation("Developer")
    assert job_id not in ref_res

    # 4. find_fix_precedent finds recorded issue
    fix_res = find_fix_precedent("client_server_contract")
    assert "Route mismatch" in fix_res

    # 5. check_known_bad catches anti-patterns
    bad_contract = check_known_bad("wiring_contract: packages: [tests]")
    assert "KNOWN ANTI-PATTERN" in bad_contract

    bad_dep = check_known_bad("dependencies: memmachine-client==0.1.5")
    assert "memmachine-client==0.1.5" in bad_dep

    bad_route = check_known_bad("Client fetch('/api/v1/stream')")
    assert "route names must align" in bad_route


# ── which jobs may be offered as a reference implementation ─────────────────
#
# find_reference_implementation had the two defects already fixed once in
# get_passed_jobs_in_scope, reintroduced because it queried the jobs table
# directly instead of reusing that filter:
#
#   1. status = 'completed' alone. partially_completed is the normal terminal
#      state here and outnumbers completed 32 to 9 in the live database.
#   2. `any(not o["passed"] for o in outcomes)` is False for an empty list, so
#      a job with nothing recorded passed the guard and was handed back as a
#      reference — while the docstring promised to fail closed.


class _Cursor:
    def __init__(self, rows, sql_seen):
        self._rows, self._sql_seen = rows, sql_seen

    def execute(self, sql, params=None):
        self._sql_seen.append(sql)

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Conn:
    def __init__(self, rows):
        self.rows, self.sql_seen = rows, []

    def cursor(self):
        return _Cursor(self.rows, self.sql_seen)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def close(self):
        pass


def _patch_store(monkeypatch, rows, outcomes, artifacts):
    """Point context_tools at a store that answers from the given fixtures."""
    conn = _Conn(rows)

    class _Store:
        def _get_connection(self):
            return conn

        def get_job_outcomes(self, jid):
            return outcomes.get(jid, [])

        def get_artifact(self, jid, name):
            return artifacts.get(jid)

    monkeypatch.setattr(
        "llamaindex_crew.tools.context_tools.PostgresContextStore", _Store
    )
    return conn


def _ok(name):
    return {"check_name": name, "passed": True}


def test_a_partially_completed_job_can_be_a_reference(monkeypatch):
    conn = _patch_store(
        monkeypatch,
        rows=[("job-partial",)],
        outcomes={"job-partial": [_ok("entrypoint"), _ok("completeness")]},
        artifacts={"job-partial": [{"path": "app/main.py"}]},
    )

    result = find_reference_implementation("Developer")

    assert "app/main.py" in result
    assert "partially_completed" in " ".join(conn.sql_seen), (
        "gating on status='completed' alone discards most of the live corpus"
    )


def test_a_job_with_no_recorded_outcomes_is_not_a_reference(monkeypatch):
    """No evidence is not evidence of passing — the promised fail-closed."""
    _patch_store(
        monkeypatch,
        rows=[("job-unverified",)],
        outcomes={},
        artifacts={"job-unverified": [{"path": "app/main.py"}]},
    )

    result = find_reference_implementation("Developer")

    assert "app/main.py" not in result
    assert "No reference implementation" in result
