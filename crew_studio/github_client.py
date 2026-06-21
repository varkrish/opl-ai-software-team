"""GitHub REST API helpers for per-user repo search and authenticated clone."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, urlunparse

import httpx

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"
_HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}


def authenticated_clone_url(https_url: str, token: str) -> str:
    """Return HTTPS clone URL with token embedded for private repo access."""
    url = https_url.strip().rstrip("/")
    if not url.endswith(".git"):
        url = f"{url}.git"
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return url
    host = parsed.hostname
    if parsed.port:
        host = f"{host}:{parsed.port}"
    netloc = f"x-access-token:{token}@{host}"
    return urlunparse((parsed.scheme, netloc, parsed.path, "", "", ""))


async def test_github_connection(token: str) -> Dict[str, Any]:
    """Verify PAT and return authenticated user profile."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{_GITHUB_API}/user",
            headers={**_HEADERS, "Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "login": data.get("login", ""),
            "name": data.get("name") or data.get("login", ""),
            "avatar_url": data.get("avatar_url", ""),
        }


def _repo_item(raw: Dict[str, Any]) -> Dict[str, Any]:
    clone_url = raw.get("clone_url") or raw.get("html_url", "")
    if clone_url and not clone_url.endswith(".git"):
        clone_url = f"{clone_url}.git"
    return {
        "name": raw.get("name", ""),
        "full_name": raw.get("full_name", ""),
        "url": raw.get("html_url", ""),
        "clone_url": clone_url,
        "private": bool(raw.get("private")),
        "description": raw.get("description") or "",
    }


async def search_repositories(token: str, q: str = "", per_page: int = 20) -> List[Dict[str, Any]]:
    """List or search repositories accessible to the token owner."""
    headers = {**_HEADERS, "Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        user_resp = await client.get(f"{_GITHUB_API}/user", headers=headers)
        user_resp.raise_for_status()
        login = user_resp.json().get("login", "")

        query = (q or "").strip()
        if query:
            search_q = f"{query} in:name user:{login} fork:true"
            resp = await client.get(
                f"{_GITHUB_API}/search/repositories",
                params={"q": search_q, "per_page": per_page, "sort": "updated"},
                headers=headers,
            )
            resp.raise_for_status()
            items = resp.json().get("items", [])
        else:
            resp = await client.get(
                f"{_GITHUB_API}/user/repos",
                params={
                    "sort": "updated",
                    "per_page": per_page,
                    "affiliation": "owner,collaborator,organization_member",
                },
                headers=headers,
            )
            resp.raise_for_status()
            items = resp.json()

        return [_repo_item(r) for r in items]


def github_host_from_url(url: str) -> Optional[str]:
    """Extract host from a GitHub HTTPS URL (github.com or GHE)."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return parsed.netloc.lower()
