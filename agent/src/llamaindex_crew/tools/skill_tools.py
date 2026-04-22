"""
SkillQueryTool — FunctionTool factory for querying the skills service.

Also provides ``prefetch_skills()`` for programmatic skill injection
into agent prompts (Designer, Tech Architect) so downstream agents
inherit framework knowledge through design_spec.md / tech_stack.md.

Usage:
    tool = SkillQueryTool(service_url="http://skills:8090", default_tags=["python"])
    # Returns a FunctionTool that agents can call to search skill docs.

    context = prefetch_skills("Build a Frappe invoicing app")
    # Returns formatted skill sections for prompt injection.
"""
import json
import logging
from pathlib import Path
from typing import List, Optional

import httpx
from llama_index.core.tools import FunctionTool

logger = logging.getLogger(__name__)


def SkillQueryTool(
    service_url: str,
    default_tags: Optional[List[str]] = None,
) -> FunctionTool:
    """Create a FunctionTool that queries the skills service over HTTP."""

    def query_skills(
        query: str,
        tags: Optional[List[str]] = None,
        top_k: int = 3,
    ) -> str:
        effective_tags = tags or default_tags
        payload: dict = {"query": query, "top_k": top_k}
        if effective_tags:
            payload["tags"] = effective_tags

        logger.info("Querying skills service: query=%r tags=%s top_k=%d", query, effective_tags, top_k)
        try:
            resp = httpx.post(f"{service_url}/query", json=payload, timeout=30)
            resp.raise_for_status()
            results = resp.json().get("results", [])
            if not results:
                logger.info("Skills query returned 0 results")
                return "No matching skills found."
            parts = [f"[{r['skill_name']}] {r['content']}" for r in results]
            logger.info("Skills query returned %d results", len(results))
            return "\n---\n".join(parts)
        except Exception:
            logger.warning("Skills service unavailable", exc_info=True)
            return ""

    return FunctionTool.from_defaults(
        fn=query_skills,
        name="skill_query",
        description=(
            "Search project skills and coding guidelines by meaning. "
            "Returns relevant skill content from indexed SKILL.md files. "
            "Optionally filter by tags (e.g. ['python', 'frappe'])."
        ),
    )


def prefetch_skills(
    vision: str,
    role: str = "general",
    extra_queries: Optional[List[str]] = None,
    top_k: int = 3,
    workspace_path: Optional[Path] = None,
) -> str:
    """Programmatically fetch skills and return formatted context for prompt injection.

    This is the core mechanism that makes Designer and Tech Architect outputs
    implementation-specific.  By injecting real skill content into their task
    prompts, we guarantee that design_spec.md and tech_stack.md contain the
    framework's actual conventions — so downstream agents (developer, frontend,
    reviewer) follow the right patterns without needing their own skill calls.

    Args:
        vision:  Project vision text used to derive semantic queries.
        role:    ``"designer"`` or ``"tech_architect"`` — controls default
                 query set.  Anything else uses a generic set.
        extra_queries:  Additional queries appended to the defaults.
        top_k:   Results per query.
        workspace_path: If set, writes ``skill_prefetch.json`` for debugging.

    Returns:
        Formatted string of ``[Skill: name]\\ncontent`` blocks separated by
        ``---``, or ``""`` when the service is unavailable.
    """
    from ..config import ConfigLoader

    try:
        config = ConfigLoader.load()
        url = getattr(config, "skills", None)
        service_url = getattr(url, "service_url", None) if url else None
        if not service_url:
            logger.info("prefetch_skills: no skills.service_url configured — skipping")
            return ""
    except Exception:
        logger.warning("prefetch_skills: config load failed — skipping", exc_info=True)
        return ""

    if role == "designer":
        queries = [
            f"{vision} design patterns UI architecture conventions",
            f"{vision} component architecture implementation patterns",
        ]
    elif role == "tech_architect":
        queries = [
            f"{vision} app folder structure scaffold conventions",
            f"{vision} coding patterns implementation architecture",
        ]
    else:
        queries = [
            f"{vision} architecture conventions patterns",
            f"{vision} folder structure implementation",
        ]

    if extra_queries:
        queries.extend(extra_queries)

    seen_skills: set[str] = set()
    sections: list[str] = []
    debug_entries: list[dict] = []

    for q in queries:
        try:
            resp = httpx.post(
                f"{service_url}/query",
                json={"query": q, "top_k": top_k},
                timeout=15,
            )
            resp.raise_for_status()
            for r in resp.json().get("results", []):
                skill_name = r["skill_name"]
                if skill_name in seen_skills:
                    continue
                seen_skills.add(skill_name)
                sections.append(f"[Skill: {skill_name}]\n{r['content']}")
                debug_entries.append({
                    "skill_name": skill_name,
                    "query": q,
                    "score": r.get("score"),
                })
        except Exception:
            logger.warning("prefetch_skills: query %r failed", q, exc_info=True)

    if workspace_path and debug_entries:
        try:
            prefetch_file = Path(workspace_path) / "skill_prefetch.json"
            existing: dict = {}
            if prefetch_file.exists():
                existing = json.loads(prefetch_file.read_text(encoding="utf-8"))
            existing[role] = debug_entries
            prefetch_file.write_text(
                json.dumps(existing, indent=2, default=str), encoding="utf-8",
            )
        except Exception:
            pass

    if sections:
        logger.info(
            "prefetch_skills [%s]: injected %d skill sections (%s)",
            role, len(sections), ", ".join(sorted(seen_skills)),
        )
    else:
        logger.info("prefetch_skills [%s]: no matching skills found", role)

    return "\n\n---\n\n".join(sections)
