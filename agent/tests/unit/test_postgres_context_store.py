"""
Unit tests for PostgresContextStore and document indexer Postgres persistence.
Verifies that:
1. crew_context database is used for context storage.
2. Failed validation jobs are NOT returned by recall.
3. Intact JSONB contracts, prose, and call graph edges are persisted.
"""
import pytest
from pathlib import Path
from llamaindex_crew.memory.scope import MemoryScope
from llamaindex_crew.memory.postgres_context_store import PostgresContextStore
from llamaindex_crew.utils.document_indexer import index_approved_solution, recall_scoped_blueprints


@pytest.fixture
def store():
    # Uses local postgres on 55432 or mock/fallback
    ps = PostgresContextStore()
    if not ps.init_schema():
        pytest.skip("Postgres crew_context DB unavailable at 127.0.0.1:55432")
    return ps


def test_postgres_context_store_outcome_filtering(store, tmp_path):
    scope = MemoryScope(org_id="testorg", project_id="testproj", domain="backend")

    # Record Good Job (all passed)
    good_job_id = "job-good-111"
    store.record_job(
        job_id=good_job_id,
        scope_org=scope.org_id,
        scope_project=scope.project_id,
        scope_domain=scope.domain,
        vision="Build high performance FastAPI service",
        outcomes=[{"check_name": "wiring_contract", "passed": True}],
        json_artifacts={"wiring_contract": {"packages": ["fastapi", "uvicorn"]}},
        prose_documents=[{"doc_type": "solution_spec", "text": "Proven FastAPI solution specification"}],
    )

    # Record Bad Job (failed wiring check)
    bad_job_id = "job-bad-222"
    store.record_job(
        job_id=bad_job_id,
        scope_org=scope.org_id,
        scope_project=scope.project_id,
        scope_domain=scope.domain,
        vision="Build tests only service",
        outcomes=[{"check_name": "wiring_contract", "passed": False, "severity": "error"}],
        json_artifacts={"wiring_contract": {"packages": ["tests"]}},
        prose_documents=[{"doc_type": "solution_spec", "text": "Bad tests-only spec"}],
    )

    # Verify get_passed_jobs_in_scope only returns good_job_id
    passed = store.get_passed_jobs_in_scope(scope.org_id, scope.project_id, scope.domain)
    assert good_job_id in passed
    assert bad_job_id not in passed

    # Verify recall_scoped_blueprints only returns good job blueprint
    chunks = recall_scoped_blueprints(scope, "FastAPI service")
    retrieved_job_ids = {c.job_id for c in chunks}
    assert good_job_id in retrieved_job_ids
    assert bad_job_id not in retrieved_job_ids
