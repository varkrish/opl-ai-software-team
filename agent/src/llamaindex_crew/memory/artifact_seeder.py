"""
Deterministic pipeline artifact seeding from validated prior job context.

Hands the pipeline proven starting blueprints (wiring contract, creation manifest,
test plan, call-graph dependencies) sourced ONLY from jobs whose relevant checks passed,
allowing the model to edit a known-good blueprint rather than inventing from scratch.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from llamaindex_crew.memory.scope import MemoryScope
from llamaindex_crew.memory.postgres_context_store import PostgresContextStore

logger = logging.getLogger(__name__)


def _package_names(contract: Dict[str, Any]) -> List[str]:
    """Package names from a wiring contract, whichever shape it was stored in.

    Current contracts use a mapping of name -> {files, owns}; some stored
    artifacts and fixtures use a list of {"name": ...}. Reading only one shape
    is what let a tests-only contract slip past the guard below.
    """
    packages = contract.get("packages")
    if isinstance(packages, dict):
        return sorted(str(k) for k in packages)
    if isinstance(packages, list):
        return [
            str(p.get("name")) if isinstance(p, dict) else str(p)
            for p in packages
        ]
    return []


# Check names below must match the keys the validator writes into
# report["checks"] — wiring_reconciliation, entrypoint, client_server_contract,
# completeness, smoke_test and so on. They were originally invented
# ("wiring_contract", "client_endpoint_alignment", "pytest"), which no validator
# emits, so once a required check had to be *recorded* to count as passed, three
# of the four seeders could never fire against real job data.


def seed_wiring_contract_from_prior(
    scope: MemoryScope,
    vision: str = "",
    stack: str = "",
    store: Optional[PostgresContextStore] = None,
) -> Optional[Dict[str, Any]]:
    """
    Seed wiring_contract.json candidate from prior job in scope.
    Filters out any job whose wiring_contract, entrypoint, or route alignment checks failed.
    Prevents tests-only contracts (e.g. job 1cec01ad failure).
    """
    db_store = store or PostgresContextStore()
    passed_job_ids = db_store.get_passed_jobs_in_scope(
        org_id=scope.org_id,
        project_id=scope.project_id,
        domain=scope.domain,
        required_checks=["wiring_reconciliation", "entrypoint", "client_server_contract"],
    )

    for job_id in passed_job_ids:
        artifact = db_store.get_artifact(job_id, "wiring_contract")
        if isinstance(artifact, dict) and artifact:
            # Reject a contract that declares nothing but tests — the job
            # 1cec01ad shape, where the whole application was missing.
            #
            # A real wiring contract stores packages as a MAPPING of
            # name -> {files, owns}. Checking only `isinstance(packages, list)`
            # meant this guard was skipped for every genuine contract, so the
            # one thing it exists to catch would have been seeded anyway. Both
            # shapes are handled because stored artifacts predate the fix.
            pkg_names = _package_names(artifact)
            if pkg_names and all(
                n.strip("/").split("/")[0] in ("tests", "test") for n in pkg_names
            ):
                logger.warning(
                    "Rejected prior wiring contract from job %s: declares only tests (%s)",
                    job_id, pkg_names,
                )
                continue
            logger.info("Seeded wiring_contract from validated prior job %s", job_id)
            return artifact

    return None


def seed_creation_manifest_from_prior(
    scope: MemoryScope,
    vision: str = "",
    stack: str = "",
    store: Optional[PostgresContextStore] = None,
) -> Optional[List[Dict[str, Any]]]:
    """
    Seed creation manifest candidate file entries from prior job in scope.
    Requires job to have passed completeness and entrypoint validation.
    """
    db_store = store or PostgresContextStore()
    passed_job_ids = db_store.get_passed_jobs_in_scope(
        org_id=scope.org_id,
        project_id=scope.project_id,
        domain=scope.domain,
        required_checks=["entrypoint", "completeness"],
    )

    for job_id in passed_job_ids:
        artifact = db_store.get_artifact(job_id, "creation_manifest")
        if isinstance(artifact, list) and artifact:
            logger.info("Seeded creation_manifest (%d files) from prior job %s", len(artifact), job_id)
            return artifact
        elif isinstance(artifact, dict) and "files" in artifact:
            logger.info("Seeded creation_manifest (%d files) from prior job %s", len(artifact["files"]), job_id)
            return artifact["files"]

    return None


def seed_test_plan_from_prior(
    scope: MemoryScope,
    vision: str = "",
    stack: str = "",
    store: Optional[PostgresContextStore] = None,
) -> Optional[str]:
    """
    Seed test plan text (including preview_command and verification steps) from prior job in scope.
    Requires job to have passed pytest and smoke validation.
    """
    db_store = store or PostgresContextStore()
    passed_job_ids = db_store.get_passed_jobs_in_scope(
        org_id=scope.org_id,
        project_id=scope.project_id,
        domain=scope.domain,
        required_checks=["smoke_test"],
    )

    for job_id in passed_job_ids:
        stack_manifest = db_store.get_artifact(job_id, "stack_manifest")
        if isinstance(stack_manifest, dict):
            preview_cmd = stack_manifest.get("preview_command") or stack_manifest.get("test_command")
            if preview_cmd:
                plan = (
                    f"## SEEDED TEST PLAN & VERIFICATION (Sourced from Job {job_id})\n"
                    f"Verified Preview Command: `{preview_cmd}`\n"
                    "Automated Test Suite: pytest --cov\n"
                )
                logger.info("Seeded test plan from prior job %s", job_id)
                return plan

    return None


def seed_contract_deps_from_prior_callgraph(
    scope: MemoryScope,
    vision: str = "",
    stack: str = "",
    store: Optional[PostgresContextStore] = None,
) -> Optional[List[Dict[str, str]]]:
    """
    Populate wiring-contract dependencies sourced from call-graph edges of prior validated jobs.
    Call-graph edges captured via refresh_call_graph (warm) and read_call_graph.
    """
    db_store = store or PostgresContextStore()
    passed_job_ids = db_store.get_passed_jobs_in_scope(
        org_id=scope.org_id,
        project_id=scope.project_id,
        domain=scope.domain,
        required_checks=["wiring_reconciliation"],
    )

    for job_id in passed_job_ids:
        edges = db_store.get_call_graph_edges(job_id)
        if edges:
            deps = []
            seen = set()
            for edge in edges:
                key = f"{edge['from_file']}->{edge['to_file']}"
                if key not in seen and edge['from_file'] != edge['to_file']:
                    seen.add(key)
                    deps.append({
                        "source_file": edge['from_file'],
                        "target_file": edge['to_file'],
                        "from_func": edge['from_func'],
                        "to_func": edge['to_func'],
                    })
            if deps:
                logger.info("Seeded %d call-graph dependency edge(s) from prior job %s", len(deps), job_id)
                return deps

    return None
