"""
Unit tests for deterministic artifact seeding from validated prior jobs.
Verifies that:
1. Candidate wiring contracts are seeded ONLY from validated prior jobs.
2. A tests-only contract (job 1cec01ad failure) is rejected and never seeded.
3. Creation manifests, test plans, and call-graph dependencies are seeded cleanly.
"""
import pytest
from llamaindex_crew.memory.scope import MemoryScope
from llamaindex_crew.memory.postgres_context_store import PostgresContextStore
from llamaindex_crew.memory.artifact_seeder import (
    seed_wiring_contract_from_prior,
    seed_creation_manifest_from_prior,
    seed_test_plan_from_prior,
    seed_contract_deps_from_prior_callgraph,
)


@pytest.fixture
def store():
    ps = PostgresContextStore()
    if not ps.init_schema():
        pytest.skip("Postgres crew_context DB unavailable at 127.0.0.1:55432")
    return ps


def test_seed_wiring_contract_prevents_tests_only_failure(store):
    scope = MemoryScope(org_id="testorg", project_id="fastapi-app", domain="web")

    # 1. Bad job 1cec01ad: produced tests-only contract
    store.record_job(
        job_id="1cec01ad-bad",
        scope_org=scope.org_id,
        scope_project=scope.project_id,
        scope_domain=scope.domain,
        vision="Test app with bad contract",
        outcomes=[{"check_name": "wiring_contract", "passed": False, "severity": "error"}],
        json_artifacts={
            "wiring_contract": {
                "language": "python",
                "packages": [{"name": "tests"}],
                "entrypoint": None,
            }
        },
    )

    # 2. Good job: proven FastAPI contract
    proven_contract = {
        "language": "python",
        "packages": [{"name": "fastapi"}, {"name": "uvicorn"}],
        "entrypoint": "backend/main.py",
    }
    store.record_job(
        job_id="proven-fastapi-good",
        scope_org=scope.org_id,
        scope_project=scope.project_id,
        scope_domain=scope.domain,
        vision="FastAPI microservice",
        outcomes=[
            {"check_name": "wiring_contract", "passed": True},
            {"check_name": "entrypoint", "passed": True},
            {"check_name": "client_endpoint_alignment", "passed": True},
        ],
        json_artifacts={
            "wiring_contract": proven_contract,
            "creation_manifest": [{"path": "backend/main.py"}, {"path": "backend/api.py"}],
            "stack_manifest": {"preview_command": "uvicorn backend.main:app --port 8080"},
        },
        call_graph_edges=[
            {"from_file": "backend/main.py", "from_func": "init", "to_file": "backend/api.py", "to_func": "get_router"},
        ],
    )

    # Test seed_wiring_contract_from_prior
    seeded = seed_wiring_contract_from_prior(scope, store=store)
    assert seeded is not None
    pkg_names = [p["name"] for p in seeded.get("packages", [])]
    assert "fastapi" in pkg_names
    assert pkg_names != ["tests"]

    # Test seed_creation_manifest_from_prior
    manifest = seed_creation_manifest_from_prior(scope, store=store)
    assert manifest is not None
    assert any(f.get("path") == "backend/main.py" for f in manifest)

    # Test seed_test_plan_from_prior
    test_plan = seed_test_plan_from_prior(scope, store=store)
    assert test_plan is not None
    assert "uvicorn backend.main:app --port 8080" in test_plan

    # Test seed_contract_deps_from_prior_callgraph
    deps = seed_contract_deps_from_prior_callgraph(scope, store=store)
    assert deps is not None
    assert any(d["source_file"] == "backend/main.py" and d["target_file"] == "backend/api.py" for d in deps)
