"""JIRA helpers for epic story creation from the workflow engine."""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)


def build_jira_backend() -> Optional[Any]:
    """Construct a Jira backend from environment variables when configured."""
    base_url = os.getenv("JIRA_BASE_URL", "").strip()
    if not base_url:
        return None

    backend_type = os.getenv("JIRA_BACKEND", "rest").strip().lower()
    try:
        if backend_type == "atlassian_mcp":
            from crew_jira_connector.jira_backends.atlassian_mcp import AtlassianMCPBackend

            token = os.getenv("JIRA_API_TOKEN") or os.getenv("JIRA_PERSONAL_ACCESS_TOKEN")
            if not token:
                return None
            return AtlassianMCPBackend(
                api_token=token,
                email=os.getenv("JIRA_EMAIL", ""),
                cloud_id=os.getenv("JIRA_CLOUD_ID", ""),
                mcp_endpoint=os.getenv("ATLASSIAN_MCP_ENDPOINT", "https://mcp.atlassian.com/v1/mcp"),
            )

        if backend_type == "local_mcp":
            from crew_jira_connector.jira_backends.local_mcp import LocalMCPBackend

            return LocalMCPBackend(
                mcp_command=os.getenv("LOCAL_MCP_COMMAND") or None,
                mcp_http_url=os.getenv("LOCAL_MCP_HTTP_URL") or None,
            )

        from crew_jira_connector.jira_backends.rest_backend import JiraRestBackend

        return JiraRestBackend(
            base_url=base_url,
            email=os.getenv("JIRA_EMAIL", ""),
            api_token=os.getenv("JIRA_API_TOKEN", ""),
            username=os.getenv("JIRA_USERNAME", ""),
            password=os.getenv("JIRA_PASSWORD", ""),
            personal_access_token=os.getenv("JIRA_PERSONAL_ACCESS_TOKEN", ""),
        )
    except Exception as exc:
        logger.warning("Failed to build JIRA backend: %s", exc, exc_info=True)
        return None


def create_stories_in_jira(
    backend: Any,
    epic_key: str,
    project_key: str,
    stories: list[dict],
) -> list[dict]:
    """Create child stories in JIRA and return metadata dicts with real keys."""
    created: list[dict] = []
    for idx, story in enumerate(stories):
        summary = (story.get("summary") or f"Story {idx + 1}").strip()
        description = (story.get("description") or summary).strip()
        try:
            key = backend.create_issue(
                project_key=project_key,
                summary=summary,
                description=description,
                issue_type=os.getenv("JIRA_STORY_ISSUE_TYPE", "Story"),
                parent_key=epic_key,
            )
            created.append({
                "key": key,
                "summary": summary,
                "description": description,
                "status": "To Do",
                "order": idx,
            })
            logger.info("Created JIRA story %s under epic %s", key, epic_key)
        except Exception as exc:
            logger.error("Failed to create JIRA story %r: %s", summary, exc, exc_info=True)
            fallback_key = story.get("key") or f"{project_key}-{idx + 1}"
            created.append({
                "key": fallback_key,
                "summary": summary,
                "description": description,
                "status": story.get("status") or "To Do",
                "order": idx,
            })
    return created
