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
        required_checks=["wiring_contract", "entrypoint", "client_endpoint_alignment"],
    )

    for job_id in passed_job_ids:
        artifact = db_store.get_artifact(job_id, "wiring_contract")
        if isinstance(artifact, dict) and artifact:
            # Check structure: contract containing only "tests" package or no entrypoint is rejected
            packages = artifact.get("packages", [])
            if isinstance(packages, list) and packages:
                pkg_names = [p.get("name") if isinstance(p, dict) else str(p) for p in packages]
                if set(pkg_names) == {"tests"} or set(pkg_names) == {"test"}:
                    logger.warning("Rejected prior candidate wiring contract for job %s: contains only tests package", job_id)
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
        required_checks=["pytest", "smoke"],
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
        required_checks=["wiring_contract"],
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
