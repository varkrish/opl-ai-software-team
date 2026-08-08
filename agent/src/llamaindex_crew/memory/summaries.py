"""
Summary generation for the context memory plane.

Raw artefacts stay per-job in the workspace; what goes into cross-job memory is
a compact, searchable summary. Each builder here has two halves:

1. ``collect_*_signals`` — gather facts from the DB and workspace. Pure data,
   no LLM, fully testable.
2. ``summarize_*`` — turn those facts into recall text. Tries a cheap LLM call
   and falls back to a deterministic template when no LLM is available or the
   call fails, so a summary is always produced.

The deterministic fallback matters: a template summary still carries the
framework, status, and failure counts that make recall useful. Losing the
summary entirely because a utility model was unreachable would be worse.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_MAX_VISION_CHARS = 500
_MAX_SPEC_CHARS = 300
_TOP_ISSUES = 5


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _read_head(path: Path, limit: int) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")[:limit].strip()
    except (OSError, UnicodeDecodeError):
        return ""


def _validation_counts(report: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Derive pass/fail counts from a validation report.

    Shape varies by workflow path, so this probes the common containers and
    reports what it found rather than assuming a schema.
    """
    if not report:
        return {"total": 0, "passed": 0, "failed_names": [], "overall": ""}

    checks = report.get("checks")
    if isinstance(checks, dict):
        items = list(checks.items())
    elif isinstance(checks, list):
        items = [(str(c.get("name") or c.get("check") or i), c) for i, c in enumerate(checks)]
    else:
        items = []

    passed = 0
    failed_names: List[str] = []
    for name, value in items:
        if isinstance(value, dict):
            ok = value.get("pass")
            if ok is None:
                ok = str(value.get("status", "")).lower() in ("pass", "passed", "ok")
        else:
            ok = bool(value)
        if ok:
            passed += 1
        else:
            failed_names.append(str(name))

    return {
        "total": len(items),
        "passed": passed,
        "failed_names": failed_names[:_TOP_ISSUES],
        "overall": str(report.get("overall") or ""),
    }


def collect_job_outcome_signals(
    job_id: str,
    *,
    job: Optional[Dict[str, Any]] = None,
    workspace_path: Optional[Path] = None,
    results: Optional[Dict[str, Any]] = None,
    job_db: Any = None,
    final_status: str = "",
) -> Dict[str, Any]:
    """
    Gather everything known about a finished job.

    Reads only signals that already exist today — no confidence score required.
    Every lookup is defensive because this runs in a post-job hook that must not
    be able to fail the job.
    """
    job = job or {}
    results = results or {}
    workspace = Path(workspace_path) if workspace_path else None

    metadata = job.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            metadata = {}

    validation_report = results.get("validation_report") or {}
    if not validation_report and workspace:
        validation_report = _read_json(workspace / "validation_report.json") or {}
    validation = _validation_counts(validation_report)

    task_validation = results.get("task_validation") or {}

    refinement_count = 0
    failed_issue_count = 0
    total_tokens = 0
    if job_db is not None:
        try:
            refinement_count = len(job_db.get_refinement_history(job_id) or [])
        except Exception as exc:  # noqa: BLE001
            logger.debug("Refinement history unavailable for %s: %s", job_id, exc)
        try:
            failed_issue_count = len(job_db.get_failed_validation_issues(job_id) or [])
        except Exception as exc:  # noqa: BLE001
            logger.debug("Validation issues unavailable for %s: %s", job_id, exc)
        try:
            for row in job_db.get_llm_usage(job_id) or []:
                total_tokens += int(row.get("input_tokens") or 0)
                total_tokens += int(row.get("output_tokens") or 0)
        except Exception as exc:  # noqa: BLE001
            logger.debug("LLM usage unavailable for %s: %s", job_id, exc)

    solution_spec = _read_head(workspace / "solution_spec.md", _MAX_SPEC_CHARS) if workspace else ""

    solutioning = metadata.get("solutioning_stats") or metadata.get("loop_state") or {}
    if not isinstance(solutioning, dict):
        solutioning = {}

    return {
        "job_id": job_id,
        "vision": str(job.get("vision") or "")[:_MAX_VISION_CHARS],
        "final_status": final_status or results.get("status") or job.get("status") or "unknown",
        "validation_total": validation["total"],
        "validation_passed": validation["passed"],
        "validation_failed_names": validation["failed_names"],
        "validation_overall": validation["overall"],
        "failed_issue_count": failed_issue_count,
        "task_validation_ok": bool(task_validation.get("valid", True)),
        "incomplete_tasks": list(task_validation.get("incomplete_tasks") or [])[:_TOP_ISSUES],
        "refinement_count": refinement_count,
        "total_tokens": total_tokens,
        "solution_spec_head": solution_spec,
        "solutioning_passes": solutioning.get("pass_count") or solutioning.get("passes"),
        "solutioning_approved": solutioning.get("approved"),
    }


def _fallback_job_outcome(signals: Dict[str, Any], scope_label: str) -> str:
    parts = [
        f"Job on {scope_label} finished with status '{signals.get('final_status')}'."
    ]
    vision = str(signals.get("vision") or "").strip()
    if vision:
        first_line = vision.splitlines()[0][:200]
        parts.append(f"Goal: {first_line}")

    total = signals.get("validation_total") or 0
    if total:
        parts.append(
            f"Validation: {signals.get('validation_passed')}/{total} checks passed."
        )
    failed = signals.get("validation_failed_names") or []
    if failed:
        parts.append("Failing checks: " + ", ".join(str(f) for f in failed) + ".")
    if signals.get("failed_issue_count"):
        parts.append(f"{signals['failed_issue_count']} unresolved validation issue(s).")
    incomplete = signals.get("incomplete_tasks") or []
    if incomplete:
        parts.append("Incomplete tasks: " + ", ".join(str(t) for t in incomplete) + ".")
    if signals.get("refinement_count"):
        parts.append(f"Needed {signals['refinement_count']} human refinement(s).")
    return " ".join(parts)


_JOB_OUTCOME_PROMPT = """Summarize this software job outcome in 2-3 sentences for future recall.

Write for an engineer starting a SIMILAR job later. Prioritise what would change
their approach: what was built, what failed validation, what needed rework.
Be specific and factual. Do not speculate. No preamble, just the summary.

Vision: {vision}
Framework/domain: {scope_label}
Final status: {final_status}
Validation: {validation_passed}/{validation_total} checks passed{validation_detail}
Unresolved validation issues: {failed_issue_count}
Incomplete tasks: {incomplete}
Human refinements needed: {refinement_count}
Solutioning: {solutioning}
Key artifact (solution_spec.md head): {solution_spec_head}
"""


def summarize_job_outcome(
    signals: Dict[str, Any],
    *,
    scope_label: str = "",
    llm_call: Optional[Any] = None,
    max_chars: int = 1200,
) -> str:
    """
    Build the job outcome recall text.

    ``llm_call`` is any callable taking a prompt string and returning text. When
    absent or failing, a deterministic template summary is returned instead.
    """
    failed = signals.get("validation_failed_names") or []
    detail = f" (failing: {', '.join(str(f) for f in failed)})" if failed else ""
    solutioning = "not run"
    if signals.get("solutioning_passes"):
        approved = signals.get("solutioning_approved")
        verdict = "approved" if approved else "not approved"
        solutioning = f"{verdict} after {signals['solutioning_passes']} pass(es)"

    summary = ""
    if llm_call is not None:
        prompt = _JOB_OUTCOME_PROMPT.format(
            vision=signals.get("vision") or "(none recorded)",
            scope_label=scope_label or "unknown",
            final_status=signals.get("final_status"),
            validation_passed=signals.get("validation_passed"),
            validation_total=signals.get("validation_total"),
            validation_detail=detail,
            failed_issue_count=signals.get("failed_issue_count"),
            incomplete=", ".join(str(t) for t in (signals.get("incomplete_tasks") or [])) or "none",
            refinement_count=signals.get("refinement_count"),
            solutioning=solutioning,
            solution_spec_head=signals.get("solution_spec_head") or "(none)",
        )
        try:
            summary = str(llm_call(prompt) or "").strip()
        except Exception as exc:  # noqa: BLE001 — fall back to template
            logger.warning("Job outcome summary LLM call failed, using template: %s", exc)
            summary = ""

    if not summary:
        summary = _fallback_job_outcome(signals, scope_label or "this stack")

    return summary[:max_chars].strip()


_DOC_SUMMARY_PROMPT = """Summarize this reference document in 2-3 sentences for future recall.

A future job in the same domain should be able to decide from your summary alone
whether this document is relevant, without re-reading it. Name concrete things:
systems, counts, versions, decisions. No preamble, just the summary.

Document name: {filename}
Domain: {scope_label}
Content excerpt:
{excerpt}
"""


def summarize_reference_doc(
    filename: str,
    excerpt: str,
    *,
    scope_label: str = "",
    llm_call: Optional[Any] = None,
    max_chars: int = 1200,
) -> str:
    """
    Build recall text for an uploaded reference document.

    Returns "" when there is no usable excerpt and no LLM — an empty summary is
    better than storing a filename with no information in it.
    """
    excerpt = (excerpt or "").strip()
    summary = ""

    if llm_call is not None and excerpt:
        prompt = _DOC_SUMMARY_PROMPT.format(
            filename=filename,
            scope_label=scope_label or "unknown",
            excerpt=excerpt[:6000],
        )
        try:
            summary = str(llm_call(prompt) or "").strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Reference doc summary LLM call failed: %s", exc)
            summary = ""

    if not summary:
        if not excerpt:
            return ""
        # Deterministic fallback: first meaningful lines of the document.
        lines = [ln.strip() for ln in excerpt.splitlines() if ln.strip()]
        summary = " ".join(lines[:3])[:400]

    return f"Reference doc '{filename}': {summary}"[:max_chars].strip()


def build_jira_context_summary(
    issue_key: str,
    summary_text: str,
    *,
    issue_type: str = "",
    mode: str = "",
    repo_url: str = "",
    has_gherkin: bool = False,
    status: str = "",
) -> str:
    """
    Build recall text for a Jira-created job.

    Deterministic — no LLM. The Jira fields are already human-written prose, so
    an LLM pass would add cost and drift without adding information.
    """
    parts = [f"Jira {issue_key}: {summary_text.strip()}."]
    if issue_type:
        parts.append(f"Type: {issue_type}.")
    if mode:
        parts.append(f"Mode: {mode}.")
    parts.append(f"Repo: {repo_url or 'none'}.")
    parts.append(f"Gherkin provided: {'yes' if has_gherkin else 'no'}.")
    if status:
        parts.append(f"Status: {status}.")
    return " ".join(parts)


def build_jira_epic_summary(
    epic_key: str,
    epic_summary: str,
    story_keys: List[str],
    *,
    mode: str = "build",
) -> str:
    """Build recall text for an epic so sibling stories can see the whole scope."""
    stories = ", ".join(story_keys) if story_keys else "none linked"
    return (
        f"Epic {epic_key}: {epic_summary.strip()}. "
        f"Child stories: {stories}. Mode: {mode}."
    )
