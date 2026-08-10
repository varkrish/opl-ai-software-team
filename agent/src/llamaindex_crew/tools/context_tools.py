"""
Agent tools for querying context memory plane when the model must make architectural choices.

Rules:
1. Every return carries its outcome (whether the prior job built/passed).
2. Whole artifacts, never fragments.
3. Filter deterministically inside the tool.
4. Fail closed — return nothing rather than a failure's architecture.
5. Concise summary plus fetch-by-id.
6. Persist failures so check_known_bad can identify anti-patterns without surfacing them as templates.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional
from llamaindex_crew.memory.postgres_context_store import PostgresContextStore

logger = logging.getLogger(__name__)


def find_similar_solutions(vision: str, stack: str = "", limit: int = 3) -> str:
    """
    Search prior solution blueprints by vision/stack.
    Returns solution summaries along with their exact check-level validation outcomes.
    """
    try:
        store = PostgresContextStore()
        # Retrieve candidate jobs
        conn = store._get_connection()
        if not conn:
            return "No prior solutions found (database unreachable)."

        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT job_id, vision, status FROM jobs ORDER BY created_at DESC LIMIT %s;", (limit * 2,))
                rows = cur.fetchall()

        if not rows:
            return "No prior solutions recorded in context memory."

        results = []
        count = 0
        for jid, vis, status in rows:
            outcomes = store.get_job_outcomes(jid)
            failed = [o for o in outcomes if not o["passed"]]

            # "No failures recorded" and "no outcomes recorded" are different
            # claims. Reporting an unverified job as PASSED ALL CHECKS is how a
            # tests-only contract came to sit in the old index at 9/10 — the
            # model reading this must be able to tell evidence from silence.
            if not outcomes:
                outcome_str = "NOT VALIDATED — no checks recorded, do not treat as proven"
            elif failed:
                outcome_str = f"FAILED CHECKS: {', '.join(f['check_name'] for f in failed)}"
            else:
                outcome_str = (
                    "PASSED: " + ", ".join(sorted({o["check_name"] for o in outcomes}))
                )
            summary = f"Job ID: {jid}\nVision: {vis}\nStatus: {status}\nValidation Outcome: {outcome_str}"
            results.append(summary)
            count += 1
            if count >= limit:
                break

        return "\n\n---\n\n".join(results)
    except Exception as exc:
        logger.error("find_similar_solutions tool error: %s", exc)
        return "Error querying prior solutions."


def get_prior_artifact(job_id: str, name: str) -> str:
    """
    Retrieve a whole intact artifact (e.g. wiring_contract, solution_spec, stack_manifest, creation_manifest) by job_id.
    Includes the job's validation outcome header.
    """
    try:
        store = PostgresContextStore()
        outcomes = store.get_job_outcomes(job_id)
        failed = [o for o in outcomes if not o["passed"]]
        outcome_header = f"Validation Outcome: PASSED ALL CHECKS" if not failed else f"Validation Outcome: FAILED CHECKS: {', '.join(f['check_name'] for f in failed)}"

        artifact = store.get_artifact(job_id, name)
        if not artifact:
            return f"Artifact {name!r} not found for job {job_id}."

        content_str = json.dumps(artifact, indent=2) if isinstance(artifact, (dict, list)) else str(artifact)
        return f"=== ARTIFACT {name!r} (Job {job_id}) ===\n{outcome_header}\n\n{content_str}"
    except Exception as exc:
        logger.error("get_prior_artifact tool error: %s", exc)
        return f"Error retrieving artifact {name} for job {job_id}."


def find_reference_implementation(role: str, stack: str = "") -> str:
    """
    Find a reference implementation file from a prior job that PASSED all validation checks.
    Fails closed: returns nothing if no passing implementation exists.
    """
    try:
        store = PostgresContextStore()
        conn = store._get_connection()
        if not conn:
            return "No reference implementation found (database unreachable)."

        # partially_completed is not a lesser job — it is the normal terminal
        # state here, outnumbering completed 32 to 9 in the live database, and
        # what matters is whether the checks passed rather than how the run
        # ended. Gating on status='completed' alone discarded most of the
        # corpus, the same defect already fixed in get_passed_jobs_in_scope.
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT job_id FROM jobs "
                    "WHERE status IN ('completed', 'partially_completed', 'completed_with_errors') "
                    "ORDER BY created_at DESC LIMIT 10;"
                )
                rows = cur.fetchall()

        for (jid,) in rows:
            outcomes = store.get_job_outcomes(jid)
            # any(...) over an empty list is False, so a job with nothing
            # recorded used to sail through the check below and be offered as a
            # reference implementation. No evidence is not evidence of passing,
            # and this function's contract is to fail closed.
            if not outcomes:
                continue
            if any(not o["passed"] for o in outcomes):
                continue  # Fail closed: skip any job with failing validation checks

            creation = store.get_artifact(jid, "creation_manifest")
            if isinstance(creation, list) and creation:
                return f"Reference implementation file list from validated job {jid}:\n" + json.dumps(creation, indent=2)

        return "No reference implementation found from a fully validated prior job."
    except Exception as exc:
        logger.error("find_reference_implementation tool error: %s", exc)
        return "Error searching reference implementations."


def find_fix_precedent(check_name: str, error: str = "") -> str:
    """
    Find how past jobs successfully resolved a specific check failure (e.g. wiring_contract, entrypoint, client_endpoint_alignment).
    """
    try:
        store = PostgresContextStore()
        conn = store._get_connection()
        if not conn:
            return "No fix precedent found (database unreachable)."

        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT job_id, description, iterations, converged FROM job_outcomes
                    WHERE check_name = %s ORDER BY created_at DESC LIMIT 5;
                """, (check_name,))
                rows = cur.fetchall()

        if not rows:
            return f"No fix precedent recorded for check {check_name!r}."

        entries = []
        for jid, desc, iters, conv in rows:
            status = "Converged / Fixed" if conv else "Unresolved"
            entries.append(f"Job {jid}: {check_name} issue ({desc or 'N/A'}) - {status} in {iters} iterations.")

        return "\n".join(entries)
    except Exception as exc:
        logger.error("find_fix_precedent tool error: %s", exc)
        return "Error searching fix precedents."


def check_known_bad(proposal: str) -> str:
    """
    Check a proposed layout, package list, or endpoint route against known anti-patterns
    recorded from failed jobs (e.g. flat layout with tests-only contract, memmachine-client==0.1.5, mismatched endpoints).
    """
    anti_patterns = []

    proposal_lower = proposal.lower()

    # Anti-pattern 1: Tests-only contract
    if '"tests"' in proposal_lower or "'tests'" in proposal_lower or "packages: [tests]" in proposal_lower:
        if "fastapi" not in proposal_lower and "flask" not in proposal_lower and "express" not in proposal_lower:
            anti_patterns.append("KNOWN ANTI-PATTERN: Proposal specifies a tests-only wiring contract with no application backend package.")

    # Anti-pattern 2: Invalid dependency versions
    if "memmachine-client==0.1.5" in proposal_lower or "memmachine-client:0.1.5" in proposal_lower:
        anti_patterns.append("KNOWN ANTI-PATTERN: memmachine-client==0.1.5 does not exist or resolve. Use >=0.3.9.")

    # Anti-pattern 3: Mismatched frontend-backend routes
    if "/api/v1/stream" in proposal_lower and "/events" not in proposal_lower:
        anti_patterns.append("KNOWN ANTI-PATTERN HIT (Job 107b3d3e precedent): Client calls /api/v1/stream but server routes /events; route names must align.")

    if not anti_patterns:
        return "PASSED ANTI-PATTERN CHECK: Proposal does not match any recorded failure precedents."

    return "WARNING - ANTI-PATTERN HIT:\n" + "\n".join(f"- {ap}" for ap in anti_patterns)
