"""
Read side of the context memory plane.

Recall happens once, at the start of solutioning, and the result is injected into
the research prompt as text. Downstream agents (PO, Designer, TechArch,
Developer) read ``solution_spec.md`` and do not query memory themselves — that
keeps retrieval isolated from code generation and means one recall per job
instead of one per agent.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_MAX_VISION_IN_QUERY = 400


def build_recall_query(vision: str, scope: Any) -> str:
    """
    Build the recall query for a job.

    Includes the framework and domain explicitly because semantic search over
    summary text alone tends to match on subject matter and miss the "what went
    wrong on this stack" memories that are the most useful ones.
    """
    framework = getattr(scope, "project_id", "") or ""
    domain = getattr(scope, "domain", "") or ""
    vision_text = " ".join(str(vision or "").split())[:_MAX_VISION_IN_QUERY]
    return (
        f"Past {framework} work in the {domain} domain relevant to: {vision_text}. "
        "What did reviewers reject or correct, what failed validation, "
        "what needed rework, what reference docs exist?"
    )


def recall_solutioning_context(
    config: Any,
    *,
    vision: str,
    job: Optional[Dict[str, Any]] = None,
    workspace_path: Optional[Path] = None,
    limit: Optional[int] = None,
) -> str:
    """
    Recall past-job context for injection into the solutioning research prompt.

    Returns "" when the memory plane is disabled, unreachable, or has nothing
    relevant — callers can concatenate the result unconditionally.
    """
    memory_config = getattr(config, "memory", None)
    if not getattr(memory_config, "enabled", False):
        return ""
    if not getattr(memory_config, "read_at_solutioning", True):
        return ""

    try:
        from .context_memory import get_context_memory

        # Two projects are searched: the framework project (job outcomes for this
        # stack) and the shared project (Jira context and reference docs, written
        # before a stack was chosen). Searching only one would silently miss half
        # the plane — see MemoryConfig.shared_project_id.
        episodes: list = []
        seen: set = set()
        scope_labels: list = []

        for shared in (False, True):
            memory = get_context_memory(
                config,
                job=job,
                workspace_path=workspace_path,
                agent_id="solution_research",
                shared_project=shared,
            )
            if not memory.enabled:
                memory.close()
                continue

            query = build_recall_query(vision, memory.scope)
            for episode in memory.search(query, limit=limit):
                content = str(episode.get("content") or "").strip()
                if content and content not in seen:
                    seen.add(content)
                    episodes.append(episode)
            scope_labels.append(memory.scope.describe())
            memory.close()

        if not episodes:
            return ""

        block = _render_block(
            episodes,
            max_chars=int(getattr(memory_config, "max_recall_chars", 4000) or 4000),
        )
        if block:
            logger.info(
                "Injected %d chars of recalled context into solutioning (%s)",
                len(block), "; ".join(scope_labels),
            )
        return block
    except Exception as exc:  # noqa: BLE001 — recall is never load-bearing
        logger.warning("Solutioning recall failed (non-fatal): %s", exc)
        return ""


def _render_block(episodes: list, *, max_chars: int) -> str:
    """
    Render merged episodes from both projects into one prompt block.

    Corrections lead. They are the most actionable thing the plane holds — a
    reviewer's own words about what was wrong last time — and the block is
    truncated to a character budget, so they must not be the lines that get cut.
    A stable sort preserves ordering within each type.
    """
    episodes = sorted(
        episodes,
        key=lambda e: 0 if (e.get("metadata") or {}).get("type") == "correction" else 1,
    )
    lines = [
        "## PAST CONTEXT — corrections, past jobs, Jira issues, and reference "
        "docs in this domain (recalled from the context memory plane)",
        "",
    ]
    for episode in episodes:
        content = str(episode.get("content") or "").strip()
        if not content:
            continue
        meta = episode.get("metadata") or {}
        label = str(meta.get("type") or "memory").replace("_", " ")
        lines.append(f"- ({label}) {content}")

    if len(lines) <= 2:
        return ""

    block = "\n".join(lines)
    if len(block) > max_chars:
        block = block[:max_chars].rstrip() + "\n- … (truncated)"
    return block + "\n"
