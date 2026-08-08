"""
Lifecycle hooks that write to the cross-job context memory plane.

These are the write points for the P3 historical knowledge plane. Each one is
fail-open and returns a bool for observability only — no caller should branch on
it, and no caller needs a try/except around it.

Hook placement note: the job outcome hook lives here rather than inside
``software_dev_workflow.run()`` because ``run()`` has five separate
completion/pause exits plus distinct epic and retry completion paths. Hooking at
the ``run_job_async`` terminal-status seam covers build, epic, and retry modes
with one call site, and only fires for genuinely terminal states (pauses are
filtered out before this point).
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_DOC_EXCERPT_CHARS = 6000
_TEXTUAL_SUFFIXES = {
    ".md", ".txt", ".rst", ".csv", ".json", ".yaml", ".yml",
    ".feature", ".html", ".xml", ".log",
}


_CORRECTION_KEYS_FIELD = "memory_correction_keys"
_MAX_TRACKED_KEYS = 200


def _memory_enabled(config: Any) -> bool:
    return bool(getattr(getattr(config, "memory", None), "enabled", False))


def _correction_key(correction: Any) -> str:
    """Stable fingerprint for one correction, used to avoid re-writing it."""
    raw = "|".join(
        str(getattr(correction, attr, "") or "")
        for attr in ("source", "instruction", "response", "file_path")
    )
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:16]


def _load_job_metadata(job: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    metadata = (job or {}).get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            return {}
    return metadata if isinstance(metadata, dict) else {}


def _record_correction_keys(
    job_id: str, job_db: Any, job: Optional[Dict[str, Any]], keys: list
) -> None:
    """
    Persist the fingerprints just written to ``jobs.metadata``.

    Resume and retry re-enter the post-job hook for the same job, so without this
    the same reviewer comment is stored two or three times and recall fills with
    duplicates of one remark.
    """
    if job_db is None or not keys:
        return
    try:
        current = job_db.get_job(job_id) or job or {}
        metadata = _load_job_metadata(current)
        existing = metadata.get(_CORRECTION_KEYS_FIELD) or []
        if not isinstance(existing, list):
            existing = []
        merged = existing + [k for k in keys if k not in existing]
        metadata[_CORRECTION_KEYS_FIELD] = merged[-_MAX_TRACKED_KEYS:]
        job_db.update_job(job_id, {"metadata": json.dumps(metadata)})
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not persist correction dedup keys for %s: %s", job_id, exc)


def _scope_label(scope: Any) -> str:
    project = getattr(scope, "project_id", "") or "unknown"
    domain = getattr(scope, "domain", "") or "general"
    return f"{project} / {domain}"


def write_job_outcome_memory(
    job_id: str,
    *,
    config: Any,
    job: Optional[Dict[str, Any]] = None,
    workspace_path: Optional[Path] = None,
    results: Optional[Dict[str, Any]] = None,
    job_db: Any = None,
    final_status: str = "",
) -> bool:
    """
    Write a job outcome summary when a job reaches a terminal state.

    Builds the summary from signals that already exist — validation report, task
    validation, refinement count, token spend, solutioning stats. Deliberately
    does not require a confidence score.
    """
    if not _memory_enabled(config):
        return False
    if not getattr(config.memory, "write_job_outcome", True):
        return False

    try:
        from src.llamaindex_crew.memory import (
            collect_job_outcome_signals,
            get_context_memory,
            summarize_job_outcome,
        )
        from src.llamaindex_crew.memory.llm import get_summary_llm_call
    except ImportError:
        try:
            from llamaindex_crew.memory import (
                collect_job_outcome_signals,
                get_context_memory,
                summarize_job_outcome,
            )
            from llamaindex_crew.memory.llm import get_summary_llm_call
        except ImportError as exc:
            logger.warning("Context memory modules unavailable: %s", exc)
            return False

    try:
        memory = get_context_memory(
            config, job=job, workspace_path=workspace_path, agent_id="post_job_hook"
        )
        if not memory.enabled:
            return False

        signals = collect_job_outcome_signals(
            job_id,
            job=job,
            workspace_path=workspace_path,
            results=results,
            job_db=job_db,
            final_status=final_status,
        )
        summary = summarize_job_outcome(
            signals,
            scope_label=_scope_label(memory.scope),
            llm_call=get_summary_llm_call(config),
            max_chars=int(getattr(config.memory, "summary_max_chars", 1200) or 1200),
        )
        wrote = memory.add(
            summary,
            memory_type="job_outcome",
            producer="post_job_hook",
            metadata={
                "job_id": job_id,
                "final_status": signals.get("final_status"),
                "validation_passed": signals.get("validation_passed"),
                "validation_total": signals.get("validation_total"),
                "failed_issue_count": signals.get("failed_issue_count"),
                "refinement_count": signals.get("refinement_count"),
                "framework": memory.scope.project_id,
                "domain": memory.scope.domain,
            },
        )
        # Pin the scope we just wrote under, so a later read cannot re-derive a
        # different framework/domain and lose this memory.
        persist_resolved_scope(job_id, job_db, memory.scope)
        memory.close()
        return wrote
    except Exception as exc:  # noqa: BLE001 — post-job hook must never fail a job
        logger.warning("Job outcome memory write raised (non-fatal): %s", exc, exc_info=True)
        return False


def _read_doc_excerpt(path: Path) -> str:
    """Read a text excerpt from an uploaded document, or "" if not textual."""
    try:
        if path.suffix.lower() not in _TEXTUAL_SUFFIXES:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")[:_DOC_EXCERPT_CHARS].strip()
    except (OSError, UnicodeDecodeError) as exc:
        logger.debug("Could not read %s for memory summary: %s", path, exc)
        return ""


def write_reference_doc_memory(
    job_id: str,
    *,
    config: Any,
    original_name: str,
    stored_path: Path,
    job: Optional[Dict[str, Any]] = None,
    workspace_path: Optional[Path] = None,
) -> bool:
    """
    Write a summary for one uploaded reference document.

    The raw file stays in ``workspace/{job_id}/docs/`` and is still indexed into
    the job-local RAG index; this only adds cross-job recall so a future job in
    the same domain knows the document exists without a re-upload.
    """
    if not _memory_enabled(config):
        return False
    if not getattr(config.memory, "write_reference_docs", True):
        return False

    try:
        from src.llamaindex_crew.memory import get_context_memory, summarize_reference_doc
        from src.llamaindex_crew.memory.llm import get_summary_llm_call
    except ImportError:
        try:
            from llamaindex_crew.memory import get_context_memory, summarize_reference_doc
            from llamaindex_crew.memory.llm import get_summary_llm_call
        except ImportError as exc:
            logger.warning("Context memory modules unavailable: %s", exc)
            return False

    try:
        excerpt = _read_doc_excerpt(Path(stored_path))
        if not excerpt:
            logger.debug(
                "Skipping memory summary for non-textual document %s", original_name
            )
            return False

        # shared_project: uploads happen before a stack is chosen, so these are
        # framework-agnostic. See MemoryConfig.shared_project_id.
        memory = get_context_memory(
            config,
            job=job,
            workspace_path=workspace_path,
            agent_id="doc_upload",
            shared_project=True,
        )
        if not memory.enabled:
            return False

        summary = summarize_reference_doc(
            original_name,
            excerpt,
            scope_label=_scope_label(memory.scope),
            llm_call=get_summary_llm_call(config),
            max_chars=int(getattr(config.memory, "summary_max_chars", 1200) or 1200),
        )
        if not summary:
            return False

        wrote = memory.add(
            summary,
            memory_type="reference_doc",
            producer="doc_upload",
            metadata={
                "job_id": job_id,
                "filename": original_name,
                "framework": memory.scope.project_id,
                "domain": memory.scope.domain,
            },
        )
        memory.close()
        return wrote
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Reference doc memory write raised (non-fatal) for %s: %s",
            original_name, exc,
        )
        return False


def write_correction_memories(
    job_id: str,
    *,
    config: Any,
    job: Optional[Dict[str, Any]] = None,
    workspace_path: Optional[Path] = None,
    job_db: Any = None,
) -> int:
    """
    Write one episode per correction recorded for this job. Returns how many landed.

    Corrections are what humans and verifiers had to fix, in the words they used.
    They go to the **framework** project rather than the shared one because they
    are stack-specific — "on Frappe, always register the hook in hooks.py" is
    advice about Frappe, and surfacing it in a Spring Boot job would be noise.

    Stored verbatim rather than summarised: the value of "we are a Postgres shop,
    do not use MongoDB" is entirely in its specifics, and an LLM paraphrase would
    blunt exactly that while adding a per-job cost. No LLM is used here.
    """
    if not _memory_enabled(config):
        return 0
    if not getattr(config.memory, "write_corrections", True):
        return 0

    try:
        from src.llamaindex_crew.memory import get_context_memory
        from src.llamaindex_crew.memory.corrections import (
            collect_corrections,
            render_correction,
        )
    except ImportError:
        try:
            from llamaindex_crew.memory import get_context_memory
            from llamaindex_crew.memory.corrections import (
                collect_corrections,
                render_correction,
            )
        except ImportError as exc:
            logger.warning("Context memory modules unavailable: %s", exc)
            return 0

    try:
        limit = int(getattr(config.memory, "max_corrections_per_job", 25) or 25)
        corrections = collect_corrections(
            job_id,
            job=job,
            workspace_path=workspace_path,
            job_db=job_db,
            limit=limit,
        )
        if not corrections:
            return 0

        # Skip anything already written for this job. Resume/retry re-enter this
        # hook; without the guard one reviewer comment is stored several times.
        already = set(_load_job_metadata(job).get(_CORRECTION_KEYS_FIELD) or [])
        pending = [(c, _correction_key(c)) for c in corrections]
        pending = [(c, k) for c, k in pending if k not in already]
        if not pending:
            logger.debug("All corrections for job %s were already written", job_id)
            return 0

        memory = get_context_memory(
            config, job=job, workspace_path=workspace_path, agent_id="correction_hook"
        )
        if not memory.enabled:
            return 0

        written = 0
        written_keys = []
        for correction, key in pending:
            text = render_correction(correction)
            if not text:
                continue
            if memory.add(
                text,
                memory_type="correction",
                producer="correction_hook",
                metadata={
                    "job_id": job_id,
                    "correction_source": correction.source,
                    "job_mode": correction.mode,
                    "outcome": correction.outcome,
                    "file_path": correction.file_path,
                    "framework": memory.scope.project_id,
                    "domain": memory.scope.domain,
                },
            ):
                written += 1
                written_keys.append(key)

        memory.close()
        _record_correction_keys(job_id, job_db, job, written_keys)
        if written:
            logger.info("Wrote %d correction memory episode(s) for job %s", written, job_id)
        return written
    except Exception as exc:  # noqa: BLE001 — post-job hook must never fail a job
        logger.warning(
            "Correction memory write raised (non-fatal) for job %s: %s", job_id, exc
        )
        return 0


def write_refinement_correction_memory(
    job_id: str,
    *,
    config: Any,
    job: Optional[Dict[str, Any]] = None,
    workspace_path: Optional[Path] = None,
    job_db: Any = None,
) -> int:
    """
    Record the refinement that just finished as a correction.

    Refinements are issued *after* a job reaches a terminal state, so the
    post-job hook has already run by the time one exists. Without this seam the
    single richest correction source — a human saying in plain words what the
    agents got wrong, now paired with what the agent did about it — would never
    reach the memory plane.

    Only the newest refinement is written (``get_refinement_history`` is
    newest-first), so calling this once per refinement does not re-write history.
    """
    if not _memory_enabled(config):
        return 0
    if not getattr(config.memory, "write_corrections", True):
        return 0

    try:
        from src.llamaindex_crew.memory import get_context_memory
        from src.llamaindex_crew.memory.corrections import (
            collect_corrections,
            render_correction,
        )
    except ImportError:
        try:
            from llamaindex_crew.memory import get_context_memory
            from llamaindex_crew.memory.corrections import (
                collect_corrections,
                render_correction,
            )
        except ImportError as exc:
            logger.warning("Context memory modules unavailable: %s", exc)
            return 0

    try:
        corrections = collect_corrections(
            job_id,
            job=job,
            workspace_path=workspace_path,
            job_db=job_db,
            sources={"refinement"},
            limit=1,
        )
        if not corrections:
            return 0

        memory = get_context_memory(
            config, job=job, workspace_path=workspace_path, agent_id="refinement_hook"
        )
        if not memory.enabled:
            return 0

        correction = corrections[0]
        wrote = memory.add(
            render_correction(correction),
            memory_type="correction",
            producer="refinement_hook",
            metadata={
                "job_id": job_id,
                "correction_source": correction.source,
                "job_mode": correction.mode,
                "outcome": correction.outcome,
                "file_path": correction.file_path,
                "framework": memory.scope.project_id,
                "domain": memory.scope.domain,
            },
        )
        memory.close()
        return 1 if wrote else 0
    except Exception as exc:  # noqa: BLE001 — must never fail a refinement
        logger.warning(
            "Refinement correction memory write raised (non-fatal) for %s: %s",
            job_id, exc,
        )
        return 0


def write_jira_context_memory(
    job_id: str,
    *,
    config: Any,
    job: Optional[Dict[str, Any]] = None,
    workspace_path: Optional[Path] = None,
) -> bool:
    """
    Write Jira issue (or epic) context for a job created from a Jira webhook.

    Written here in the backend rather than in the Jira connector on purpose. The
    connector does not know the job's ``owner_id``, so a connector-side write
    would land under a different org scope than everything else about the same
    job — memories that exist but can never be recalled. The connector's
    contribution is the ``jira_*`` metadata it already sends with ``create_job``.

    Goes to the shared project because the issue describes what to build, not
    which framework will be used.
    """
    if not _memory_enabled(config):
        return False

    job = job or {}
    metadata = job.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            metadata = {}
    if not isinstance(metadata, dict):
        return False

    issue_key = metadata.get("jira_issue_key") or metadata.get("jira_epic_key")
    if not issue_key:
        return False

    try:
        from src.llamaindex_crew.memory import (
            build_jira_context_summary,
            build_jira_epic_summary,
            get_context_memory,
        )
    except ImportError:
        try:
            from llamaindex_crew.memory import (
                build_jira_context_summary,
                build_jira_epic_summary,
                get_context_memory,
            )
        except ImportError as exc:
            logger.warning("Context memory modules unavailable: %s", exc)
            return False

    try:
        memory = get_context_memory(
            config,
            job=job,
            workspace_path=workspace_path,
            agent_id="jira_webhook",
            shared_project=True,
        )
        if not memory.enabled:
            return False

        vision = str(job.get("vision") or "")
        headline = vision.strip().splitlines()[0][:300] if vision.strip() else issue_key
        epic_key = metadata.get("jira_epic_key")
        stories = metadata.get("jira_stories") or []

        if epic_key and stories:
            story_keys = [
                str(s.get("key"))
                for s in stories
                if isinstance(s, dict) and s.get("key")
            ]
            summary = build_jira_epic_summary(
                str(epic_key), headline, story_keys, mode=str(metadata.get("mode") or "build")
            )
            memory_type = "jira_epic"
        else:
            summary = build_jira_context_summary(
                str(issue_key),
                headline,
                issue_type=str(metadata.get("jira_issue_type") or ""),
                mode=str(metadata.get("mode") or ""),
                repo_url=str(metadata.get("repo_url") or ""),
                has_gherkin=bool(metadata.get("has_gherkin")),
            )
            memory_type = "jira_context"

        wrote = memory.add(
            summary,
            memory_type=memory_type,
            producer="jira_webhook",
            metadata={
                "job_id": job_id,
                "issue_key": str(issue_key),
                "project_key": str(metadata.get("jira_project_key") or ""),
                "domain": memory.scope.domain,
            },
        )
        memory.close()
        return wrote
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Jira context memory write raised (non-fatal) for %s: %s", issue_key, exc
        )
        return False


def persist_resolved_scope(job_id: str, job_db: Any, scope: Any) -> None:
    """
    Persist the resolved framework/domain back onto the job row.

    Scope fragmentation is the main failure mode of this plane: if the framework
    or domain is re-derived differently on a later read, memories written under
    the first spelling become unreachable. Writing the resolved values once makes
    every subsequent read agree.
    """
    if job_db is None or scope is None:
        return
    try:
        job = job_db.get_job(job_id) or {}
        metadata = job.get("metadata") or {}
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        if not isinstance(metadata, dict):
            metadata = {}

        changed = False
        for key, value in (
            ("framework", getattr(scope, "project_id", "")),
            ("domain", getattr(scope, "domain", "")),
        ):
            if value and metadata.get(key) != value:
                metadata[key] = value
                changed = True

        if changed:
            job_db.update_job(job_id, {"metadata": json.dumps(metadata)})
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not persist resolved memory scope for %s: %s", job_id, exc)
