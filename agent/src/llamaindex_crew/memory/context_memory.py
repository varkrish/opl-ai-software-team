"""
Fail-open wrapper around the MemMachine client.

Every method here swallows transport, import, and server errors and logs them at
warning level. A job must never fail because the memory plane is down — memory
is an accelerant, not a dependency. Callers therefore do not need try/except
around these calls, and there is no "memory unavailable" error path to handle.

Read/write shape (memmachine-client 0.3.9):

    client = MemMachineClient(base_url=..., api_key=...)
    client.get_or_create_project(org_id, project_id)
    mem = Memory(client, org_id=..., project_id=..., metadata={"group_id": domain})
    mem.add(content, metadata={...}, producer=...)
    mem.search(query, limit=...)   # auto-filtered by instance metadata

Instance metadata auto-filters searches, so only scope keys go there; see
:mod:`llamaindex_crew.memory.scope`.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Optional

from .scope import MemoryScope, resolve_scope, slugify

logger = logging.getLogger(__name__)

# Emitted once per process so a disabled/absent memory plane does not spam logs.
_warned: set = set()
_warn_lock = threading.Lock()


def _warn_once(key: str, message: str, *args: Any) -> None:
    with _warn_lock:
        if key in _warned:
            return
        _warned.add(key)
    logger.warning(message, *args)


def _stringify_metadata(metadata: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """
    Coerce metadata to ``dict[str, str]``.

    The client raises TypeError on non-string filter values and the server
    rejects non-string metadata, so numbers and booleans must be stringified
    before they ever reach it.
    """
    if not metadata:
        return {}
    out: Dict[str, str] = {}
    for key, value in metadata.items():
        if value is None:
            continue
        if isinstance(value, bool):
            out[str(key)] = "true" if value else "false"
        elif isinstance(value, (int, float, str)):
            out[str(key)] = str(value)
        else:
            out[str(key)] = str(value)[:500]
    return out


class ContextMemory:
    """
    Scoped handle on the cross-job memory plane for one job.

    Construct via :func:`get_context_memory`, which resolves scope from the job
    row. When ``enabled`` is False every method is a no-op returning an empty
    result, so call sites stay branch-free.
    """

    def __init__(
        self,
        scope: MemoryScope,
        *,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout_seconds: int = 15,
        search_limit: int = 5,
        search_score_threshold: Optional[float] = None,
        max_recall_chars: int = 4000,
        enabled: bool = True,
    ) -> None:
        self.scope = scope
        self.base_url = (base_url or "").rstrip("/") or None
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.search_limit = search_limit
        self.search_score_threshold = search_score_threshold
        self.max_recall_chars = max_recall_chars
        self._requested = enabled
        self._client = None
        self._memory = None
        self._init_failed = False

    # ── lifecycle ────────────────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        """True when writes and reads should be attempted."""
        return bool(self._requested and self.base_url and not self._init_failed)

    def _memory_handle(self):
        """Lazily build the Memory handle; returns None when unavailable."""
        if not self.enabled:
            return None
        if self._memory is not None:
            return self._memory

        try:
            from memmachine_client import MemMachineClient, Memory
        except ImportError as exc:
            self._init_failed = True
            _warn_once(
                "import",
                "Context memory disabled: memmachine-client not installed (%s). "
                "Install with: pip install memmachine-client",
                exc,
            )
            return None

        try:
            client = MemMachineClient(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout_seconds,
            )
            # Projects must exist before Memory can read or write to them.
            client.get_or_create_project(
                org_id=self.scope.org_id,
                project_id=self.scope.project_id,
                description=f"OPL Crew memories for {self.scope.project_id}",
            )
            memory = Memory(
                client,
                org_id=self.scope.org_id,
                project_id=self.scope.project_id,
                metadata=self.scope.instance_metadata(),
            )
        except Exception as exc:  # noqa: BLE001 — fail-open by design
            self._init_failed = True
            _warn_once(
                "connect",
                "Context memory unavailable at %s (%s: %s). Jobs continue without recall.",
                self.base_url,
                type(exc).__name__,
                exc,
            )
            return None

        self._client = client
        self._memory = memory
        logger.info("Context memory ready — %s", self.scope.describe())
        return self._memory

    def close(self) -> None:
        client, self._client, self._memory = self._client, None, None
        if client is None:
            return
        try:
            client.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Context memory close raised (ignored): %s", exc)

    def __enter__(self) -> "ContextMemory":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    # ── writes ───────────────────────────────────────────────────────────────

    def add(
        self,
        content: str,
        *,
        memory_type: str,
        metadata: Optional[Dict[str, Any]] = None,
        producer: Optional[str] = None,
    ) -> bool:
        """
        Write one episode. Returns True when it was accepted.

        ``memory_type`` is a free-form label ("job_outcome", "jira_context",
        "reference_doc") stored as ``metadata["type"]`` so reads can filter by it.
        """
        content = (content or "").strip()
        if not content:
            return False

        memory = self._memory_handle()
        if memory is None:
            return False

        episode_metadata = _stringify_metadata(
            {"type": memory_type, **(metadata or {})}
        )
        try:
            memory.add(
                content,
                role="assistant",
                producer=producer or self.scope.agent_id or "opl-crew",
                metadata=episode_metadata,
            )
        except Exception as exc:  # noqa: BLE001 — fail-open by design
            logger.warning(
                "Context memory write failed (type=%s, %s): %s",
                memory_type, self.scope.describe(), exc,
            )
            return False

        logger.info(
            "Context memory wrote %s (%s, %d chars)",
            memory_type, self.scope.describe(), len(content),
        )
        return True

    # ── reads ────────────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        *,
        limit: Optional[int] = None,
        memory_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Recall memories for a query, scoped to this job's org/project/domain.

        Returns a list of ``{"content": str, "metadata": dict}`` dicts — never
        raises, returns ``[]`` when the plane is unavailable or finds nothing.
        """
        query = (query or "").strip()
        if not query:
            return []

        memory = self._memory_handle()
        if memory is None:
            return []

        filter_dict = {"metadata.type": memory_type} if memory_type else None
        try:
            result = memory.search(
                query,
                limit=limit or self.search_limit,
                score_threshold=self.search_score_threshold,
                filter_dict=filter_dict,
            )
        except Exception as exc:  # noqa: BLE001 — fail-open by design
            logger.warning(
                "Context memory search failed (%s): %s", self.scope.describe(), exc
            )
            return []

        episodes = _extract_episodes(result)
        logger.info(
            "Context memory recalled %d episode(s) for %r (%s)",
            len(episodes), query[:60], self.scope.describe(),
        )
        return episodes

    def recall_block(
        self,
        query: str,
        *,
        limit: Optional[int] = None,
        heading: str = "PAST CONTEXT (from previous jobs in this domain)",
    ) -> str:
        """
        Recall and render memories as a prompt-injectable text block.

        Returns "" when there is nothing to inject, so callers can concatenate
        unconditionally. Truncated to ``max_recall_chars``.
        """
        episodes = self.search(query, limit=limit)
        if not episodes:
            return ""

        lines = [f"## {heading}", ""]
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
        if len(block) > self.max_recall_chars:
            block = block[: self.max_recall_chars].rstrip() + "\n- … (truncated)"
        return block + "\n"


def _extract_episodes(result: Any) -> List[Dict[str, Any]]:
    """
    Normalise a SearchResult into plain dicts.

    The server response shape varies across MemMachine versions, so this probes
    the documented containers rather than assuming one schema.
    """
    if result is None:
        return []

    candidates: List[Any] = []
    for attr in ("episodes", "results", "memories", "content"):
        value = getattr(result, attr, None)
        if isinstance(value, list) and value:
            candidates = value
            break
    if not candidates and isinstance(result, dict):
        for key in ("episodes", "results", "memories", "content"):
            value = result.get(key)
            if isinstance(value, list) and value:
                candidates = value
                break
    if not candidates and isinstance(result, list):
        candidates = result

    episodes: List[Dict[str, Any]] = []
    for item in candidates:
        if isinstance(item, str):
            episodes.append({"content": item, "metadata": {}})
            continue
        if isinstance(item, dict):
            content = item.get("content") or item.get("text") or item.get("episode")
            metadata = item.get("metadata") or {}
        else:
            content = getattr(item, "content", None) or getattr(item, "text", None)
            metadata = getattr(item, "metadata", None) or {}
        if content:
            episodes.append(
                {
                    "content": str(content),
                    "metadata": metadata if isinstance(metadata, dict) else {},
                }
            )
    return episodes


def get_context_memory(
    config: Any = None,
    *,
    job: Optional[Dict[str, Any]] = None,
    workspace_path: Optional[Path] = None,
    agent_id: Optional[str] = None,
    scope: Optional[MemoryScope] = None,
    shared_project: bool = False,
) -> ContextMemory:
    """
    Build a :class:`ContextMemory` for a job.

    Always returns an object — a disabled one when the memory plane is off or
    unconfigured — so callers never branch on availability.

    Set ``shared_project=True`` for memories written before a stack is chosen
    (Jira context, reference docs). Those go to ``memory.shared_project_id``
    instead of the framework project, so they are not stranded under a fallback
    framework that later reads never search. See the config field docs.
    """
    memory_config = getattr(config, "memory", None)

    enabled = bool(getattr(memory_config, "enabled", False))
    base_url = (
        getattr(memory_config, "base_url", None)
        or os.getenv("MEMMACHINE_BASE_URL")
        or os.getenv("MEMORY_BACKEND_URL")
    )
    api_key = getattr(memory_config, "api_key", None) or os.getenv("MEMMACHINE_API_KEY")

    if scope is None:
        scope = resolve_scope(
            job,
            workspace_path=workspace_path,
            agent_id=agent_id,
            default_org_id=getattr(memory_config, "default_org_id", "default") or "default",
            default_project_id=(
                getattr(memory_config, "default_project_id", "unknown-framework")
                or "unknown-framework"
            ),
        )

    if shared_project:
        shared_id = getattr(memory_config, "shared_project_id", "shared-context") or "shared-context"
        scope = replace(scope, project_id=slugify(shared_id, "shared-context"))

    if enabled and not base_url:
        _warn_once(
            "no-base-url",
            "Context memory is enabled but no base_url is set "
            "(config.memory.base_url or MEMMACHINE_BASE_URL). Running without recall.",
        )

    return ContextMemory(
        scope,
        base_url=base_url,
        api_key=api_key,
        timeout_seconds=int(getattr(memory_config, "timeout_seconds", 15) or 15),
        search_limit=int(getattr(memory_config, "search_limit", 5) or 5),
        search_score_threshold=getattr(memory_config, "search_score_threshold", None),
        max_recall_chars=int(getattr(memory_config, "max_recall_chars", 4000) or 4000),
        enabled=enabled,
    )
