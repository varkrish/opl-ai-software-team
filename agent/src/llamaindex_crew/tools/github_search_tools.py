"""GitHub search tools for the solutioning research pass."""
from __future__ import annotations

import base64
import json
import logging
import re
from typing import Optional

import httpx
from llama_index.core.tools import FunctionTool

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"
_NO_TOKEN_MSG = "GitHub search unavailable -- no GITHUB_TOKEN configured"
_REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _format_repo(item: dict) -> dict:
    license_info = item.get("license") or {}
    return {
        "full_name": item.get("full_name", ""),
        "description": item.get("description") or "",
        "stars": item.get("stargazers_count", 0),
        "license": license_info.get("spdx_id") or license_info.get("name") or "",
        "topics": item.get("topics") or [],
    }


def GitHubSearchReposTool(token: Optional[str], max_calls: int = 10) -> FunctionTool:
    """Search GitHub repositories (top 5 results per query)."""

    if not token:
        def unavailable(query: str) -> str:
            return _NO_TOKEN_MSG

        return FunctionTool.from_defaults(
            fn=unavailable,
            name="github_search_repos",
            description="Search GitHub repositories for reference implementations.",
        )

    call_count = {"n": 0}

    def search_repos(query: str) -> str:
        if call_count["n"] >= max_calls:
            return f"GitHub search rate limit reached (max_calls={max_calls})"
        call_count["n"] += 1
        try:
            resp = httpx.get(
                f"{_GITHUB_API}/search/repositories",
                params={"q": query, "sort": "stars", "order": "desc", "per_page": 5},
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {token}",
                },
                timeout=30,
            )
            resp.raise_for_status()
            items = resp.json().get("items", [])[:5]
            return json.dumps([_format_repo(item) for item in items], indent=2)
        except httpx.HTTPStatusError as exc:
            logger.warning("GitHub search HTTP error: %s", exc)
            return f"GitHub search error: HTTP {exc.response.status_code}"
        except Exception as exc:
            logger.warning("GitHub search failed", exc_info=True)
            return f"GitHub search error: {exc}"

    return FunctionTool.from_defaults(
        fn=search_repos,
        name="github_search_repos",
        description=(
            "Search GitHub for reference repositories. "
            "Returns top 5 matches with name, description, stars, license, and topics."
        ),
    )


def GitHubRepoReadmeTool(token: Optional[str]) -> FunctionTool:
    """Fetch and decode a repository README (first 4000 chars)."""

    if not token:
        def unavailable(repo: str) -> str:
            return _NO_TOKEN_MSG

        return FunctionTool.from_defaults(
            fn=unavailable,
            name="github_repo_readme",
            description="Fetch a GitHub repository README for architecture reference.",
        )

    def fetch_readme(repo: str) -> str:
        if not _REPO_PATTERN.match((repo or "").strip()):
            return "Invalid repository format — expected owner/repo"
        owner, name = repo.strip().split("/", 1)
        try:
            resp = httpx.get(
                f"{_GITHUB_API}/repos/{owner}/{name}/readme",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {token}",
                },
                timeout=30,
            )
            resp.raise_for_status()
            payload = resp.json()
            raw = payload.get("content") or ""
            if payload.get("encoding") == "base64":
                decoded = base64.b64decode(raw).decode("utf-8", errors="replace")
            else:
                decoded = raw
            return decoded[:4000]
        except httpx.HTTPStatusError as exc:
            logger.warning("GitHub README HTTP error: %s", exc)
            return f"GitHub README error: HTTP {exc.response.status_code}"
        except Exception as exc:
            logger.warning("GitHub README fetch failed", exc_info=True)
            return f"GitHub README error: {exc}"

    return FunctionTool.from_defaults(
        fn=fetch_readme,
        name="github_repo_readme",
        description="Fetch the README from a GitHub repository (owner/repo).",
    )
