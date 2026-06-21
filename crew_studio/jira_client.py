"""Jira REST helpers with Server vs Cloud auto-detection (mirrors crew_jira_connector)."""
from __future__ import annotations

import logging
import re
from typing import Any, Literal, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

DeploymentType = Literal["server", "cloud"]

# Cache deployment type per base URL to avoid repeated serverInfo calls
_deployment_cache: dict[str, DeploymentType] = {}


def build_search_jql(q: str, project: str = "", *, deployment: DeploymentType = "cloud") -> str:
    """Build a bounded JQL string for issue search."""
    conditions: list[str] = []
    if q.strip():
        safe_q = q.strip().replace('"', '\\"')
        if deployment == "server":
            text_clauses = [f'summary ~ "{safe_q}"', f'description ~ "{safe_q}"']
        else:
            text_clauses = [f'summary ~ "{safe_q}"', f'text ~ "{safe_q}"']
        if re.match(r"^[A-Za-z][A-Za-z0-9]+-\d+$", safe_q):
            text_clauses.append(f'key = "{safe_q.upper()}"')
        conditions.append(f'({" OR ".join(text_clauses)})')
    if project.strip():
        conditions.append(f'project = "{project.strip().upper()}"')
    if conditions:
        return " AND ".join(conditions) + " ORDER BY updated DESC"
    if deployment == "server":
        return "ORDER BY updated DESC"
    return "updated >= -90d ORDER BY updated DESC"


async def detect_deployment(base_url: str, auth: Tuple[str, str]) -> DeploymentType:
    """Detect Jira Server vs Cloud via /rest/api/2/serverInfo."""
    base = base_url.rstrip("/")
    if base in _deployment_cache:
        return _deployment_cache[base]
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{base}/rest/api/2/serverInfo",
                auth=auth,
                headers={"Accept": "application/json"},
            )
        resp.raise_for_status()
        deployment = resp.json().get("deploymentType", "").lower()
        kind: DeploymentType = "server" if deployment == "server" else "cloud"
        logger.info(
            "Jira detected at %s: deploymentType=%s -> api=%s",
            base, deployment or "unknown", "2" if kind == "server" else "3",
        )
    except Exception as exc:
        logger.warning("Failed to detect Jira deployment at %s (%s), defaulting to Cloud", base, exc)
        kind = "cloud"
    _deployment_cache[base] = kind
    return kind


def _field_name(field: Optional[dict]) -> str:
    return (field or {}).get("name", "")


def _parse_issues(data: dict, base_url: str) -> list[dict[str, Any]]:
    base = base_url.rstrip("/")
    return [
        {
            "key": issue["key"],
            "summary": issue["fields"].get("summary", ""),
            "status": _field_name(issue["fields"].get("status")),
            "issue_type": _field_name(issue["fields"].get("issuetype")),
            "priority": _field_name(issue["fields"].get("priority")),
            "project": (issue["fields"].get("project") or {}).get("key", ""),
            "url": f"{base}/browse/{issue['key']}",
        }
        for issue in data.get("issues", [])
    ]


async def _search_server(
    client: httpx.AsyncClient,
    base_url: str,
    auth: Tuple[str, str],
    jql: str,
    max_results: int,
) -> httpx.Response:
    """Jira Server / Data Center: POST /rest/api/2/search."""
    return await client.post(
        f"{base_url.rstrip('/')}/rest/api/2/search",
        json={
            "jql": jql,
            "maxResults": max_results,
            "fields": ["summary", "status", "issuetype", "priority", "assignee", "project"],
        },
        auth=auth,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )


async def _search_cloud_v3_jql(
    client: httpx.AsyncClient,
    base_url: str,
    auth: Tuple[str, str],
    jql: str,
    max_results: int,
) -> httpx.Response:
    """Jira Cloud (2024+): GET /rest/api/3/search/jql."""
    return await client.get(
        f"{base_url.rstrip('/')}/rest/api/3/search/jql",
        params={
            "jql": jql,
            "maxResults": max_results,
            "fields": "summary,status,issuetype,priority,assignee,project",
        },
        auth=auth,
        headers={"Accept": "application/json"},
    )


async def _search_cloud_v3_legacy(
    client: httpx.AsyncClient,
    base_url: str,
    auth: Tuple[str, str],
    jql: str,
    max_results: int,
) -> httpx.Response:
    """Older Jira Cloud: POST /rest/api/3/search."""
    return await client.post(
        f"{base_url.rstrip('/')}/rest/api/3/search",
        json={
            "jql": jql,
            "maxResults": max_results,
            "fields": ["summary", "status", "issuetype", "priority", "assignee", "project"],
        },
        auth=auth,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )


async def search_issues(
    base_url: str,
    email: str,
    api_token: str,
    jql: str,
    max_results: int = 20,
) -> dict[str, Any]:
    """Search Jira issues; picks API version based on deployment type.

    Returns {"issues": [...], "total": int, "has_more": bool}.
    Raises httpx.HTTPStatusError for non-retryable HTTP failures.
    """
    auth = (email, api_token)
    deployment = await detect_deployment(base_url, auth)

    async with httpx.AsyncClient(timeout=15) as client:
        if deployment == "server":
            resp = await _search_server(client, base_url, auth, jql, max_results)
        else:
            resp = await _search_cloud_v3_jql(client, base_url, auth, jql, max_results)
            if resp.status_code == 410:
                logger.info("Cloud search/jql unavailable, falling back to POST /rest/api/3/search")
                resp = await _search_cloud_v3_legacy(client, base_url, auth, jql, max_results)
            if resp.status_code == 410:
                logger.info("Cloud POST /search unavailable, falling back to GET /rest/api/2/search")
                resp = await client.get(
                    f"{base_url.rstrip('/')}/rest/api/2/search",
                    params={
                        "jql": jql,
                        "maxResults": max_results,
                        "fields": "summary,status,issuetype,priority,assignee,project",
                    },
                    auth=auth,
                    headers={"Accept": "application/json"},
                )

    if resp.status_code == 401:
        raise httpx.HTTPStatusError("Unauthorized", request=resp.request, response=resp)
    resp.raise_for_status()
    data = resp.json()
    issues = _parse_issues(data, base_url)
    return {
        "issues": issues,
        "total": data.get("total", len(issues)),
        "has_more": not data.get("isLast", True) if deployment == "cloud" else len(issues) >= max_results,
    }
