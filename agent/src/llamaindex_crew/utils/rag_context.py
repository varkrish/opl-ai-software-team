"""Shared RAG phase queries and context retrieval for workflow agents."""
from __future__ import annotations

from typing import Any

PHASE_QUERIES: dict[str, str] = {
    "meta": (
        "Project vision, goals, constraints, target audience, requirements, "
        "scope, and success criteria"
    ),
    "product_owner": (
        "User stories, acceptance criteria, functional requirements, features, "
        "personas, and business rules"
    ),
    "designer": (
        "Architecture, bounded contexts, domain model, components, data flow, "
        "UI design, API design, and integration points"
    ),
    "tech_architect": (
        "Technology stack, file structure, frameworks, libraries, deployment, "
        "database schema, and technical constraints"
    ),
    "development": (
        "Implementation details, code patterns, APIs, data models, file paths, "
        "and technical specifications"
    ),
    "story_progress": (
        "Previously completed story call relationships, implemented classes, "
        "functions, APIs, and cross-file dependencies from prior stories"
    ),
}


def _prompt_limits(config: Any) -> Any:
    return getattr(config, "prompt_limits", None) if config else None


def _rag_setting(config: Any, name: str, default: int) -> int:
    pl = _prompt_limits(config)
    return int(getattr(pl, name, default)) if pl else default


def get_phase_rag_context(indexer: Any, phase: str, config: Any = None, extra_query: str = "") -> str:
    """Retrieve formatted RAG context for a workflow phase."""
    if indexer is None or not getattr(indexer, "has_index", False):
        return ""
    base_query = PHASE_QUERIES.get(phase, PHASE_QUERIES["development"])
    query = f"{base_query}. {extra_query}".strip()
    top_k_key = f"rag_top_k_{phase}"
    pl = _prompt_limits(config)
    if pl and hasattr(pl, top_k_key):
        top_k = int(getattr(pl, top_k_key))
    else:
        top_k = _rag_setting(config, "rag_top_k_default", 6)
    max_chars = _rag_setting(config, "max_rag_context_chars", 32_000)
    return indexer.retrieve_formatted(query, top_k=top_k, max_chars=max_chars)
