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
    return re.sub(r"^https://", f"https://{token}@", remote_url, count=1)


def parse_github_slug(remote_url: str) -> Optional[str]:
    m = re.search(r"github\.com[/:](.+?/.+?)(?:\.git)?$", remote_url)
    return m.group(1) if m else None


def create_github_repo(
    token: str,
    repo_name: str,
    org: Optional[str] = None,
    private: bool = True,
) -> Optional[str]:
    if not token:
        return None
    repo_name = repo_name.strip().lower().replace(" ", "-")
    repo_name = re.sub(r"[^a-z0-9._-]", "-", repo_name)
    if org:
        url = f"https://api.github.com/orgs/{org}/repos"
    else:
        url = "https://api.github.com/user/repos"
    payload = json.dumps({"name": repo_name, "private": private}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        return data.get("clone_url")
    except urllib.error.HTTPError as e:
        logger.warning("GitHub repo creation failed: %s", e)
        return None
    except Exception as e:
        logger.warning("GitHub repo creation error: %s", e)
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
        summaries = [str(pi.summary) for pi in info]
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
