"""
SkillQueryTool — FunctionTool factory for querying the skills service.

Usage:
    tool = SkillQueryTool(service_url="http://skills:8090", default_tags=["python"])
    # Returns a FunctionTool that agents can call to search skill docs.
"""
import logging
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
