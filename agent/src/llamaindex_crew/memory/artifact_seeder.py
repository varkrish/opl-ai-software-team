"""
Deterministic pipeline artifact seeding from validated prior job context.

Hands the pipeline proven starting blueprints (wiring contract, test plan
execution configuration, call-graph dependencies) sourced ONLY from jobs whose
relevant checks passed, allowing the model to edit a known-good blueprint rather
than inventing from scratch.

Seed only what the pipeline would otherwise author. Anything it derives — the
creation manifest, from the contract — must be left to the derivation.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from llamaindex_crew.memory.scope import MemoryScope
from llamaindex_crew.memory.postgres_context_store import PostgresContextStore
from llamaindex_crew.tools.test_tools import parse_test_plan

logger = logging.getLogger(__name__)

# The runnable half of a test plan — the same keys ``run_tests`` and the preview
# runner read. Everything else in test_plan.md is narrative that a new job must
# write for itself.
_EXECUTION_KEYS = (
    "backend_test_command",
    "frontend_test_command",
    "backend_test_dir",
    "frontend_test_dir",
    "test_framework_backend",
    "test_framework_frontend",
    "preview_command",
)


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


# There is deliberately no creation-manifest seeder.
#
# The manifest is not authored — build_creation_manifest() derives it from the
# wiring contract plus supplementary paths. Seeding the contract therefore
# already carries the structure forward, and seeding a file list on top of a
# freshly derived one would override a deterministic derivation with a list
# belonging to a different vision.
#
# The version that existed here was worse than redundant. It required only
# entrypoint and completeness, while the contract seeder requires
# wiring_reconciliation as well — so it fired precisely on the jobs whose
# structure had been rejected. Job e4abf072 is that case: entrypoint and
# completeness passed, wiring_reconciliation failed, and it would have supplied
# a file list from a contract that did not match its own filesystem.
#
# Agents that want a known-good file list have find_reference_implementation,
# which demands a job with no failing check at all and offers the list as a
# reference rather than forcing it into the pipeline.


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
        # The commands live in test_plan.md, not stack_manifest — reading
        # ``preview_command`` off the manifest, where nothing has ever written
        # it, meant this seeder could not fire even for a job whose smoke_test
        # passed.
        plan_text = db_store.get_prose_document(job_id, "test_plan")
        if plan_text:
            config = {
                k: v for k, v in parse_test_plan(plan_text).items()
                if k in _EXECUTION_KEYS and v
            }
            if config.get("backend_test_command") or config.get("preview_command"):
                logger.info("Seeded test plan config from prior validated job %s", job_id)
                lines = "\n".join(f"{k}: {config[k]}" for k in _EXECUTION_KEYS if k in config)
                return (
                    f"## PROVEN EXECUTION CONFIGURATION (job {job_id}, smoke_test passed)\n"
                    f"These commands ran successfully against this stack. Reuse them,\n"
                    f"adjusting only paths that genuinely differ in this project.\n\n"
                    f"{lines}\n"
                )
            # A plan with prose but no runnable configuration is not a seed.
            # Handing over 5 KB of test-strategy narrative would consume most of
            # a 14b model's attention to say nothing it can act on.

        # Older records predate prose storage; fall back to the manifest.
        stack_manifest = db_store.get_artifact(job_id, "stack_manifest")
        if isinstance(stack_manifest, dict):
            preview_cmd = stack_manifest.get("preview_command") or stack_manifest.get("test_command")
            if preview_cmd:
                logger.info("Seeded preview command from prior job %s", job_id)
                return (
                    f"## PROVEN EXECUTION CONFIGURATION (job {job_id}, smoke_test passed)\n"
                    f"preview_command: {preview_cmd}\n"
                )

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
