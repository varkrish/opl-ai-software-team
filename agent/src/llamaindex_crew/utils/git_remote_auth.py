"""Git remote authentication and auto-push helpers."""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class PushResult:
    success: bool
    message: str
    summaries: list[str] = field(default_factory=list)


def is_auto_push_enabled() -> bool:
    if os.getenv("ENABLE_GIT", "true").lower() not in ("true", "1", "yes"):
        return False
    return os.getenv("ENABLE_AUTO_PUSH", "false").lower() in ("true", "1", "yes")


def is_auto_create_repo_enabled() -> bool:
    return os.getenv("AUTO_CREATE_REPO", "false").lower() in ("true", "1", "yes")


def inject_push_credentials(remote_url: str, token: str) -> str:
    if not token or not remote_url.startswith("https://"):
        return remote_url
    after_scheme = remote_url.split("://", 1)[1] if "://" in remote_url else ""
    if "@" in after_scheme.split("/", 1)[0]:
        return remote_url
    # Prefer x-access-token so GitHub accepts fine-grained + classic PATs.
    return re.sub(r"^https://", f"https://x-access-token:{token}@", remote_url, count=1)


def parse_github_slug(remote_url: str) -> Optional[str]:
    m = re.search(r"github\.com[/:](.+?/.+?)(?:\.git)?$", remote_url)
    return m.group(1) if m else None


def sanitize_github_repo_name(repo_name: str) -> str:
    name = (repo_name or "").strip().lower().replace(" ", "-")
    return re.sub(r"[^a-z0-9._-]", "-", name).strip("-._") or "crew-ai-project"


def _github_api_json(
    method: str,
    url: str,
    token: str,
    payload: Optional[dict] = None,
) -> tuple[Optional[dict], Optional[int], str]:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
            return (json.loads(body) if body else {}), resp.status, ""
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            err_body = str(e)
        return None, e.code, err_body
    except Exception as e:
        return None, None, str(e)


def github_login_for_token(token: str) -> Optional[str]:
    data, _status, _err = _github_api_json("GET", "https://api.github.com/user", token)
    if not data:
        return None
    login = (data.get("login") or "").strip()
    return login or None


def create_github_repo(
    token: str,
    repo_name: str,
    org: Optional[str] = None,
    private: bool = True,
) -> Optional[str]:
    if not token:
        return None
    repo_name = sanitize_github_repo_name(repo_name)
    if org:
        url = f"https://api.github.com/orgs/{org}/repos"
    else:
        url = "https://api.github.com/user/repos"
    data, status, err = _github_api_json(
        "POST",
        url,
        token,
        {"name": repo_name, "private": private},
    )
    if data and data.get("clone_url"):
        return data["clone_url"]

    # Name already exists (or similar validation) — reuse the existing repo URL.
    if status == 422:
        owner = org or github_login_for_token(token)
        if owner:
            existing, get_status, get_err = _github_api_json(
                "GET",
                f"https://api.github.com/repos/{owner}/{repo_name}",
                token,
            )
            if existing and existing.get("clone_url"):
                logger.info(
                    "GitHub repo %s/%s already exists — reusing clone_url",
                    owner,
                    repo_name,
                )
                return existing["clone_url"]
            logger.warning(
                "GitHub repo exists but could not fetch %s/%s (status=%s): %s",
                owner,
                repo_name,
                get_status,
                get_err[:300],
            )
    logger.warning("GitHub repo creation failed (status=%s): %s", status, (err or "")[:300])
    return None

def ensure_remote(
    repo,
    token: str,
    org: Optional[str] = None,
    repo_name: Optional[str] = None,
    remote_name: str = "origin",
) -> tuple[bool, str]:
    """Ensure repo has a remote; optionally create GitHub repo first."""
    if repo.remotes:
        return True, repo.remotes[remote_name].url if remote_name in [r.name for r in repo.remotes] else repo.remotes[0].url

    if not is_auto_create_repo_enabled() or not token:
        return False, "No remote configured"

    name = repo_name or Path(repo.working_dir).name or "crew-ai-project"
    clone_url = create_github_repo(token, name, org=org)
    if not clone_url:
        return False, "Failed to create GitHub repository"

    repo.create_remote(remote_name, clone_url)
    return True, clone_url


def push_with_token(
    repo,
    remote_name: str = "origin",
    branch_name: Optional[str] = None,
    token: Optional[str] = None,
) -> PushResult:
    token = (token or os.getenv("GITHUB_TOKEN", "")).strip()
    if not repo.remotes:
        ok, msg = ensure_remote(repo, token, org=os.getenv("GITHUB_ORG") or None)
        if not ok:
            return PushResult(False, msg)

    try:
        remote = repo.remote(remote_name)
    except ValueError:
        return PushResult(False, f"Remote '{remote_name}' not found")

    original_url = remote.url
    auth_url = inject_push_credentials(original_url, token) if token else original_url
    if auth_url != original_url:
        remote.set_url(auth_url)

    try:
        if branch_name:
            info = remote.push(f"HEAD:refs/heads/{branch_name}")
        else:
            info = remote.push()
        # GitPython often does not raise on rejected pushes — inspect flags.
        try:
            from git import PushInfo
            error_mask = (
                PushInfo.ERROR
                | PushInfo.REMOTE_FAILURE
                | PushInfo.REMOTE_REJECTED
            )
        except Exception:
            PushInfo = None  # type: ignore[misc, assignment]
            error_mask = 1024 | 16 | 8

        if not info:
            return PushResult(
                False,
                f"Push to {remote_name} returned no result — remote may be missing or unreachable",
            )

        summaries: list[str] = []
        errors: list[str] = []
        for pi in info:
            summary = str(getattr(pi, "summary", "") or pi)
            summaries.append(summary)
            flags = int(getattr(pi, "flags", 0) or 0)
            if flags & error_mask:
                errors.append(summary or f"push flags={flags}")
        if errors:
            return PushResult(
                False,
                f"Push failed: {'; '.join(errors)}",
                summaries,
            )
        return PushResult(True, f"Pushed to {remote_name}. {'; '.join(summaries)}", summaries)
    except Exception as e:
        err = str(e)
        if hasattr(e, "stderr") and e.stderr:
            raw_err = e.stderr.strip() if isinstance(e.stderr, str) else str(e.stderr)
            err = re.sub(r"https://[^@]+@", "https://***@", raw_err)
        return PushResult(False, f"Push failed: {err}")
    finally:
        if auth_url != original_url:
            remote.set_url(original_url)

def maybe_auto_push_after_commit(workspace: Path) -> str:
    if not is_auto_push_enabled():
        return ""

    try:
        import git as gitmodule
    except ImportError:
        return "\n⚠️ Push skipped: git module not available"

    git_dir = workspace / ".git"
    if not git_dir.is_dir():
        return "\n⚠️ Push skipped: not a git repository"

    try:
        repo = gitmodule.Repo(workspace)
    except Exception as e:
        return f"\n⚠️ Push skipped: {e}"

    remote_name = os.getenv("AUTO_PUSH_REMOTE", "origin")
    branch_name = os.getenv("AUTO_PUSH_BRANCH", "").strip() or None
    result = push_with_token(repo, remote_name=remote_name, branch_name=branch_name)
    if result.success:
        return f"\n✅ {result.message}"
    logger.warning("Auto-push failed: %s", result.message)
    return f"\n⚠️ {result.message}"
