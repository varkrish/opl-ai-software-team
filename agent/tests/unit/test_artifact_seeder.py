"""
Unit tests for deterministic artifact seeding from validated prior jobs.
Verifies that:
1. Candidate wiring contracts are seeded ONLY from validated prior jobs.
2. A tests-only contract (job 1cec01ad failure) is rejected and never seeded.
3. Creation manifests, test plans, and call-graph dependencies are seeded cleanly.
"""
import uuid

import pytest
from llamaindex_crew.memory.scope import MemoryScope
from llamaindex_crew.memory.postgres_context_store import PostgresContextStore
from llamaindex_crew.memory.artifact_seeder import (
    seed_wiring_contract_from_prior,
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
    scope = MemoryScope(org_id="testorg", project_id="fastapi-app", domain="seeder-" + uuid.uuid4().hex[:8])

    # 1. Bad job 1cec01ad: produced tests-only contract
    store.record_job(
        job_id="1cec01ad-bad",
        scope_org=scope.org_id,
        scope_project=scope.project_id,
        scope_domain=scope.domain,
        vision="Test app with bad contract",
        outcomes=[{"check_name": "wiring_reconciliation", "passed": False, "severity": "error"}],
        json_artifacts={
            "wiring_contract": {
                "language": "python",
                "packages": {"tests": {"files": ["tests/test_a.py"], "owns": []}},
                "entrypoint": None,
            }
        },
    )

    # 2. Good job: proven FastAPI contract
    proven_contract = {
        "language": "python",
        "packages": {"app": {"files": ["backend/main.py"]}, "api": {"files": ["backend/api.py"]}},
        "entrypoint": "backend/main.py",
    }
    store.record_job(
        job_id="proven-fastapi-good",
        scope_org=scope.org_id,
        scope_project=scope.project_id,
        scope_domain=scope.domain,
        vision="FastAPI microservice",
        outcomes=[
            {"check_name": "wiring_reconciliation", "passed": True},
            {"check_name": "entrypoint", "passed": True},
            {"check_name": "client_server_contract", "passed": True},
            {"check_name": "completeness", "passed": True},
            {"check_name": "smoke_test", "passed": True},
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
    pkg_names = sorted(seeded.get("packages", {}))
    assert "app" in pkg_names
    assert pkg_names != ["tests"], "the 1cec01ad shape must never be seeded"

    # There is no creation-manifest seeder: the manifest is derived from the
    # contract, so seeding the contract already carries the structure over.

    # Test seed_test_plan_from_prior
    test_plan = seed_test_plan_from_prior(scope, store=store)
    assert test_plan is not None
    assert "uvicorn backend.main:app --port 8080" in test_plan

    # Test seed_contract_deps_from_prior_callgraph
    deps = seed_contract_deps_from_prior_callgraph(scope, store=store)
    assert deps is not None
    assert any(d["source_file"] == "backend/main.py" and d["target_file"] == "backend/api.py" for d in deps)


# ── the test plan comes from test_plan.md, not stack_manifest ───────────────
#
# Live job e4abf072 stored a 5 683-character test_plan.md whose last nine lines
# are the only runnable part. `preview_command` has never once been written into
# stack_manifest.json, so the seeder that read it there could not fire even for
# a job whose smoke_test passed.

_LIVE_PLAN = """# 1. Test Strategy

## 1.1 Testing Pyramid
The service layer is the business core; unit tests give fast feedback.
Integration tests use FastAPI's TestClient against an in-memory SQLite DB.

# 3. Execution Configuration
backend_test_command: pip install -r requirements.txt && pytest
frontend_test_command: echo no frontend tests
backend_test_dir: tests
frontend_test_dir: .
test_framework_backend: pytest
test_framework_frontend: none
preview_command: pip install -r requirements.txt && uvicorn main:app --port 8000
"""


class _ProseStore:
    """Store stub: one qualifying job, whatever prose and artifacts are given."""

    def __init__(self, prose=None, artifacts=None):
        self._prose = prose or {}
        self._artifacts = artifacts or {}

    def get_passed_jobs_in_scope(self, **_kw):
        return ["job-1"]

    def get_prose_document(self, job_id, doc_type):
        return self._prose.get(doc_type)

    def get_artifact(self, job_id, name):
        return self._artifacts.get(name)


def test_the_test_plan_is_sourced_from_stored_prose():
    seeded = seed_test_plan_from_prior(
        MemoryScope(org_id="o", project_id="p", domain="d"),
        store=_ProseStore(prose={"test_plan": _LIVE_PLAN}),
    )

    assert seeded is not None, "reading preview_command off stack_manifest never fired"
    assert "uvicorn main:app --port 8000" in seeded
    assert "pip install -r requirements.txt && pytest" in seeded


def test_only_the_runnable_configuration_is_handed_over():
    """
    The narrative is the new job's to write. Replaying 5 KB of test-strategy
    prose would spend most of a 14b model's attention saying nothing it can act
    on — recall should shrink the prompt, not fill it.
    """
    seeded = seed_test_plan_from_prior(
        MemoryScope(org_id="o", project_id="p", domain="d"),
        store=_ProseStore(prose={"test_plan": _LIVE_PLAN}),
    )

    assert "Testing Pyramid" not in seeded
    assert "TestClient" not in seeded
    assert len(seeded) < 1000, f"seeded {len(seeded)} chars of a {len(_LIVE_PLAN)}-char plan"


def test_a_plan_with_no_runnable_commands_is_not_seeded():
    """Prose alone is not a seed — there is nothing for the next job to reuse."""
    assert seed_test_plan_from_prior(
        MemoryScope(org_id="o", project_id="p", domain="d"),
        store=_ProseStore(prose={"test_plan": "# 1. Test Strategy\n\nWrite good tests.\n"}),
    ) is None


def test_a_record_predating_prose_storage_falls_back_to_the_manifest():
    seeded = seed_test_plan_from_prior(
        MemoryScope(org_id="o", project_id="p", domain="d"),
        store=_ProseStore(artifacts={"stack_manifest": {"preview_command": "npm start"}}),
    )

    assert seeded is not None and "npm start" in seeded
