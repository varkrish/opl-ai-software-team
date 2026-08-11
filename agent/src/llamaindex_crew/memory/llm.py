"""
Utility-model access for memory summary generation.

Summaries are 2-3 sentence calls that run once per job or per uploaded document,
so they should never use the frontier model. Rather than add a fourth model tier,
this routes through the existing manager/worker/reviewer tiers — point
``memory.summary_agent_type`` at the cheapest one you have configured.

Returns ``None`` rather than raising when no LLM is reachable; the summary
builders fall back to deterministic templates in that case.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_VALID_TIERS = ("manager", "worker", "reviewer")


def get_summary_llm_call(config: Any = None) -> Optional[Callable[[str], str]]:
    """
    Build a ``prompt -> text`` callable backed by the configured utility model.

    Returns None when summaries are disabled (``summary_agent_type: none``) or
    when no LLM can be constructed, so callers can pass the result straight to
    ``summarize_*(llm_call=...)``.
    """
    memory_config = getattr(config, "memory", None)
    tier = str(getattr(memory_config, "summary_agent_type", "reviewer") or "reviewer").lower()

    if tier in ("none", "off", "disabled", ""):
        logger.debug("Memory summary LLM disabled by config; using template summaries.")
        return None

    if tier not in _VALID_TIERS:
        logger.warning(
            "Unknown memory.summary_agent_type %r — falling back to 'reviewer'. "
            "Valid values: %s, none.",
            tier, ", ".join(_VALID_TIERS),
        )
        tier = "reviewer"

    try:
        from ..utils.llm_config import get_llm_for_agent

        llm = get_llm_for_agent(tier, config)
    except Exception as exc:  # noqa: BLE001 — template fallback is acceptable
        logger.warning(
            "Could not build memory summary LLM (%s: %s); using template summaries.",
            type(exc).__name__, exc,
        )
        return None

    if llm is None:
        return None

    def _call(prompt: str) -> str:
        from llama_index.core.llms import ChatMessage, MessageRole

        response = llm.chat([ChatMessage(role=MessageRole.USER, content=prompt)])
        message = getattr(response, "message", None)
        return str(getattr(message, "content", response) or "").strip()

    return _call
