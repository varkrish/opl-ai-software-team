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
