"""
Epic memory indexing: after each story commit, read the tldr call graph delta
and index it into the shared DocumentIndexer so subsequent story Dev agents
can retrieve prior call relationships via semantic RAG search.

Public API:
    index_story_memory(workspace_path, document_indexer, story_key,
                       story_index, story_summary) -> None
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..tools.tldr_tools import (
    detect_tldr_lang,
    format_call_graph_delta,
    read_call_graph,
    refresh_call_graph,
)

logger = logging.getLogger(__name__)

_SNAPSHOT_FILE = "epic_graph_snapshot.json"
_PROGRESS_FILE = "epic_progress.md"


def _load_snapshot(workspace_path: Path) -> set[tuple]:
    """Load the previous call graph edge set from the snapshot file."""
    snapshot = workspace_path / _SNAPSHOT_FILE
    if not snapshot.exists():
        return set()
    try:
        edges = json.loads(snapshot.read_text(encoding="utf-8"))
        return {
            (e.get("from_file", ""), e.get("from_func", ""),
             e.get("to_file", ""),   e.get("to_func", ""))
            for e in edges
            if isinstance(e, dict)
        }
    except Exception as exc:
        logger.warning("epic_memory: corrupt snapshot at %s (%s) — treating as empty", snapshot, exc)
        return set()


def _save_snapshot(workspace_path: Path, edges: list[dict]) -> None:
    try:
        (workspace_path / _SNAPSHOT_FILE).write_text(
            json.dumps(edges, indent=2), encoding="utf-8"
        )
    except Exception as exc:
        logger.warning("epic_memory: could not save snapshot: %s", exc)


def _append_progress_md(
    workspace_path: Path,
    story_key: str,
    story_index: int,
    story_summary: str,
    new_edges: list[dict],
) -> None:
    entry_lines = [
        f"\n## Story {story_key} (index {story_index})",
        f"Task: {story_summary[:300].strip()}",
    ]
    if new_edges:
        entry_lines.append(f"New call relationships ({len(new_edges)}):")
        for e in new_edges[:20]:
            entry_lines.append(
                f"  {e.get('from_file', '?')}::{e.get('from_func', '?')}"
                f" -> {e.get('to_file', '?')}::{e.get('to_func', '?')}"
            )
        if len(new_edges) > 20:
            entry_lines.append(f"  ... and {len(new_edges) - 20} more")
    entry_lines.append("---")

    progress_file = workspace_path / _PROGRESS_FILE
    try:
        with open(progress_file, "a", encoding="utf-8") as f:
            f.write("\n".join(entry_lines) + "\n")
    except Exception as exc:
        logger.warning("epic_memory: could not append to %s: %s", _PROGRESS_FILE, exc)


def index_story_memory(
    workspace_path: Path,
    document_indexer: Any,
    story_key: str,
    story_index: int,
    story_summary: str,
) -> None:
    """Index call graph delta and a progress note after a story commit.

    - Runs `tldr structure` to refresh the call graph cache.
    - Reads .tldr/cache/call_graph*.json and computes the delta vs the previous snapshot.
    - Indexes the delta as doc_type="story_calls" under source "story:{key}:calls".
    - Always indexes a compact progress note as doc_type="story_progress".
    - Saves the updated snapshot and appends to epic_progress.md.
    - Silently degrades if tldr cache is absent or indexer raises.
    """
    workspace_path = Path(workspace_path)
    logger.info(
        "epic_memory: starting memory indexing for story %s (index %d)",
        story_key, story_index,
    )

    # 1. Refresh the tldr call graph cache, then read it
    lang = detect_tldr_lang(workspace_path)
    logger.debug("epic_memory: detected language %r for workspace %s", lang, workspace_path)
    refresh_call_graph(workspace_path, lang)
    current_edges = read_call_graph(workspace_path)
    previous_keys = _load_snapshot(workspace_path)

    logger.info(
        "epic_memory: call graph has %d total edges, %d in previous snapshot",
        len(current_edges), len(previous_keys),
    )

    new_edges = [
        e for e in current_edges
        if (e.get("from_file", ""), e.get("from_func", ""),
            e.get("to_file", ""),   e.get("to_func", "")) not in previous_keys
    ]

    if not current_edges:
        logger.warning(
            "epic_memory: no tldr call graph found for story %s — "
            "only progress note will be indexed (install tldr or ensure DevAgent runs tldr tools)",
            story_key,
        )
    else:
        logger.info(
            "epic_memory: %d new call edges in story %s (delta from %d total)",
            len(new_edges), story_key, len(current_edges),
        )

    # 2. Index call graph delta (only when there are new edges)
    delta_text = format_call_graph_delta(new_edges, story_key)
    calls_indexed = 0
    if delta_text:
        try:
            calls_indexed = document_indexer.index_text(
                delta_text,
                f"story:{story_key}:calls",
                doc_type="story_calls",
                extra_metadata={"story_key": story_key, "story_index": story_index},
            )
            logger.info(
                "epic_memory: indexed %d chunk(s) of call graph delta for story %s",
                calls_indexed or 0, story_key,
            )
        except Exception as exc:
            logger.warning("epic_memory: could not index call delta for %s: %s", story_key, exc)

    # 3. Always index a compact progress note
    progress_note = (
        f"Story {story_key} (index {story_index}) completed.\n"
        f"Task: {story_summary[:300].strip()}\n"
        f"New call relationships in this story: {len(new_edges)}"
    )
    progress_indexed = 0
    try:
        progress_indexed = document_indexer.index_text(
            progress_note,
            f"story:{story_key}:progress",
            doc_type="story_progress",
            extra_metadata={"story_key": story_key, "story_index": story_index},
        )
        logger.info(
            "epic_memory: indexed progress note for story %s (%d chunk(s))",
            story_key, progress_indexed or 0,
        )
    except Exception as exc:
        logger.warning("epic_memory: could not index progress note for %s: %s", story_key, exc)

    # 4. Persist index to disk
    try:
        document_indexer.finalize()
        rag_sources = getattr(document_indexer, "source_count", "?")
        logger.info(
            "epic_memory: RAG index persisted — total indexed sources: %s", rag_sources,
        )
    except Exception as exc:
        logger.warning("epic_memory: indexer.finalize() failed for %s: %s", story_key, exc)

    # 5. Save snapshot and human-readable log
    _save_snapshot(workspace_path, current_edges)
    _append_progress_md(workspace_path, story_key, story_index, story_summary, new_edges)

    logger.info(
        "epic_memory: complete for story %s — %d call edges indexed, snapshot saved, "
        "epic_progress.md updated",
        story_key, len(new_edges),
    )
