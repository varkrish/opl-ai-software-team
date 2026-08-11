"""
Correction harvesting — what humans and verifiers had to fix.

Job outcome summaries record *that* a job struggled. Corrections record *what
anyone had to fix*, in the words they used, which is the difference between a
memory plane that reports history and one that changes future behaviour.

Corrections arise in every job mode, not just refine:

    build / greenfield  plan review feedback, solution review feedback,
                        solution_critique_pass_N.json, test-bed critique
    import / fix        the fix instruction, driven through a refinement
    refine              refinement prompt paired with the agent's response
    migration           migration issue description + hint
    refactor            per-file refactor instruction

Most of this text is already persisted and was simply never read. The collector
is deliberately non-LLM and deterministic: these are human-written or
verifier-written sentences already, so paraphrasing them would add cost and
drift while removing the specificity that makes them worth recalling.

Every source is independently guarded — this runs inside a post-job hook, so a
failure in one source must not lose the others and must never raise.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 25
MAX_INSTRUCTION_CHARS = 2000
MAX_RESPONSE_CHARS = 1000
_CRITIQUE_GLOB = "solution_critique_pass_*.json"
_PASS_NUMBER = re.compile(r"solution_critique_pass_(\d+)\.json$")

# Human-readable labels; the raw slug is kept in metadata for filtering.
_SOURCE_LABELS = {
    "plan_review": "human plan review",
    "solution_review": "human solution review",
    "refinement": "human refinement",
    "solution_critique": "solution critique",
    "test_critique": "test critique",
    "migration_issue": "migration issue",
    "refactor_task": "refactor instruction",
}

# Which sources carry a human's own words. Machine critique is useful but a
# human correction outranks it when recall has to be trimmed.
_HUMAN_SOURCES = {"plan_review", "solution_review", "refinement"}


@dataclass
class Correction:
    """One thing that had to be fixed, and (where known) what was done about it."""

    source: str
    mode: str = ""
    instruction: str = ""
    response: str = ""
    file_path: str = ""
    outcome: str = ""
    at: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_human(self) -> bool:
        return self.source in _HUMAN_SOURCES


def _clean(text: Any, limit: int) -> str:
    """Normalise whitespace and bound length."""
    if text is None:
        return ""
    value = " ".join(str(text).split())
    if len(value) > limit:
        # Budget the ellipsis inside the limit so the result never exceeds it.
        value = value[: limit - 1].rstrip() + "…"
    return value


def _normalise_timestamp(value: Any) -> str:
    """
    Undo the double-JSON encoding in historical feedback rows.

    ``software_dev_workflow`` wrapped an already-serialised isoformat string in
    ``json.dumps``, so older rows carry ``'"2026-08-09T..."'`` with literal
    quotes. The writer is fixed, but stored rows still need unwrapping.
    """
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            return text.strip('"')
        if isinstance(decoded, str):
            return decoded
    return text


def _job_metadata(job: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    metadata = (job or {}).get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            return {}
    return metadata if isinstance(metadata, dict) else {}


def _safely(source_name: str, fn: Callable[[], List[Correction]]) -> List[Correction]:
    """Run one collector, swallowing its failure so siblings still contribute."""
    try:
        return fn() or []
    except Exception as exc:  # noqa: BLE001 — post-job hook must not raise
        logger.warning("Correction source %r failed (skipped): %s", source_name, exc)
        return []


# ── Individual sources ───────────────────────────────────────────────────────


def _from_feedback_history(
    metadata: Dict[str, Any], key: str, source: str, mode: str
) -> List[Correction]:
    entries = metadata.get(key) or []
    if not isinstance(entries, list):
        return []

    corrections = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        instruction = _clean(entry.get("feedback"), MAX_INSTRUCTION_CHARS)
        if not instruction:
            continue
        corrections.append(
            Correction(
                source=source,
                mode=mode,
                instruction=instruction,
                at=_normalise_timestamp(entry.get("at")),
            )
        )
    return corrections


def _from_test_critique(metadata: Dict[str, Any], mode: str) -> List[Correction]:
    loop_state = metadata.get("loop_state") or {}
    if not isinstance(loop_state, dict):
        return []
    critique = _clean(loop_state.get("current_critique"), MAX_INSTRUCTION_CHARS)
    if not critique:
        return []
    return [
        Correction(
            source="test_critique",
            mode=mode,
            instruction=critique,
            metadata={"test_iteration": str(loop_state.get("test_iteration") or "")},
        )
    ]


def _from_critique_passes(workspace_path: Optional[Path], mode: str) -> List[Correction]:
    if not workspace_path:
        return []
    workspace = Path(workspace_path)
    if not workspace.is_dir():
        return []

    def pass_number(path: Path) -> int:
        match = _PASS_NUMBER.search(path.name)
        return int(match.group(1)) if match else 0

    corrections = []
    # Sort numerically: lexical globbing puts pass_10 before pass_2.
    for path in sorted(workspace.glob(_CRITIQUE_GLOB), key=pass_number):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            logger.debug("Skipping unreadable critique file %s", path.name)
            continue
        if not isinstance(data, dict) or data.get("approved"):
            # An approving critique taught nobody anything.
            continue

        points = data.get("must_fix") or data.get("issues") or []
        if isinstance(points, str):
            points = [points]
        text = _clean("; ".join(str(p) for p in points if p), MAX_INSTRUCTION_CHARS)
        if not text:
            continue

        corrections.append(
            Correction(
                source="solution_critique",
                mode=mode,
                instruction=text,
                metadata={"pass": str(pass_number(path))},
            )
        )
    return corrections


def _from_refinements(job_db: Any, job_id: str, mode: str) -> List[Correction]:
    rows = job_db.get_refinement_history(job_id) or []
    corrections = []
    for row in rows:
        status = str(row.get("status") or "").lower()
        if status not in ("completed", "failed"):
            # Still running — no outcome to learn from yet.
            continue
        instruction = _clean(row.get("prompt"), MAX_INSTRUCTION_CHARS)
        if not instruction:
            continue
        # On failure the error explains what the agent could not do, which is the
        # signal; prefer an explicit response when one was recorded.
        response = row.get("response") or (row.get("error") if status == "failed" else "")
        corrections.append(
            Correction(
                source="refinement",
                mode=mode,
                instruction=instruction,
                response=_clean(response, MAX_RESPONSE_CHARS),
                file_path=str(row.get("file_path") or ""),
                outcome=status,
                at=_normalise_timestamp(row.get("created_at")),
            )
        )
    return corrections


def _from_migration_issues(job_db: Any, job_id: str, mode: str) -> List[Correction]:
    corrections = []
    for row in job_db.get_migration_issues(job_id) or []:
        title = _clean(row.get("title"), 200)
        description = _clean(row.get("description"), MAX_INSTRUCTION_CHARS)
        if not (title or description):
            continue
        instruction = f"{title}: {description}" if title and description else (title or description)
        corrections.append(
            Correction(
                source="migration_issue",
                mode=mode,
                instruction=_clean(instruction, MAX_INSTRUCTION_CHARS),
                response=_clean(row.get("migration_hint"), MAX_RESPONSE_CHARS),
                metadata={"severity": str(row.get("severity") or "")},
            )
        )
    return corrections


def _from_refactor_tasks(job_db: Any, job_id: str, mode: str) -> List[Correction]:
    corrections = []
    for row in job_db.get_refactor_tasks(job_id) or []:
        instruction = _clean(row.get("instruction"), MAX_INSTRUCTION_CHARS)
        if not instruction:
            continue
        corrections.append(
            Correction(
                source="refactor_task",
                mode=mode,
                instruction=instruction,
                file_path=str(row.get("file_path") or ""),
                metadata={"action": str(row.get("action") or "")},
            )
        )
    return corrections


# ── Public API ───────────────────────────────────────────────────────────────


def collect_corrections(
    job_id: str,
    *,
    job: Optional[Dict[str, Any]] = None,
    workspace_path: Optional[Path] = None,
    job_db: Any = None,
    limit: int = DEFAULT_LIMIT,
    sources: Optional[Any] = None,
) -> List[Correction]:
    """
    Gather every correction recorded for a job, across all modes.

    ``sources`` optionally restricts which collectors run (e.g. ``{"refinement"}``
    at the post-refinement seam). A skipped source is never queried, so a filtered
    call does not pay for — or fail on — data it would discard.

    Returns at most ``limit`` corrections, preferring human ones when trimming: a
    person explaining what was wrong outranks a verifier restating a failure.
    Never raises: a broken source is skipped, not propagated.
    """
    metadata = _job_metadata(job)
    mode = str(metadata.get("job_mode") or metadata.get("mode") or "").strip()
    wanted = set(sources) if sources else None

    def enabled(name: str) -> bool:
        return wanted is None or name in wanted

    corrections: List[Correction] = []
    if enabled("plan_review"):
        corrections += _safely(
            "plan_review",
            lambda: _from_feedback_history(
                metadata, "plan_feedback_history", "plan_review", mode
            ),
        )
    if enabled("solution_review"):
        corrections += _safely(
            "solution_review",
            lambda: _from_feedback_history(
                metadata, "solution_feedback_history", "solution_review", mode
            ),
        )
    if enabled("solution_critique"):
        corrections += _safely(
            "solution_critique", lambda: _from_critique_passes(workspace_path, mode)
        )
    if enabled("test_critique"):
        corrections += _safely("test_critique", lambda: _from_test_critique(metadata, mode))

    if job_db is not None:
        if enabled("refinement"):
            corrections += _safely(
                "refinement", lambda: _from_refinements(job_db, job_id, mode)
            )
        if enabled("migration_issue"):
            corrections += _safely(
                "migration_issue", lambda: _from_migration_issues(job_db, job_id, mode)
            )
        if enabled("refactor_task"):
            corrections += _safely(
                "refactor_task", lambda: _from_refactor_tasks(job_db, job_id, mode)
            )

    if len(corrections) > limit:
        # Stable sort keeps within-source ordering (critique pass order matters).
        corrections.sort(key=lambda c: 0 if c.is_human else 1)
        corrections = corrections[:limit]

    return corrections


def render_correction(correction: Correction, *, max_chars: int = 900) -> str:
    """
    Render one correction as recall text.

    Deliberately verbatim rather than summarised: the value of "we are a Postgres
    shop, do not use MongoDB" is in the specifics, and an LLM paraphrase would
    blunt exactly that.
    """
    label = _SOURCE_LABELS.get(correction.source, correction.source.replace("_", " "))
    parts = [f"[{label}]"]
    if correction.mode:
        parts.append(f"({correction.mode} job)")
    if correction.file_path:
        parts.append(f"on {correction.file_path}")
    parts.append(f"— {correction.instruction}")

    text = " ".join(parts)
    if correction.response:
        verb = "Attempted (failed)" if correction.outcome == "failed" else "Resolved by"
        text = f"{text} {verb}: {correction.response}"
    elif correction.outcome == "failed":
        text = f"{text} (attempt failed)"

    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text
