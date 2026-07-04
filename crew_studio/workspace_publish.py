"""Select workspace files suitable for user-facing export (download ZIP, GitHub push)."""

from __future__ import annotations

import fnmatch
import logging
import os
import re
from pathlib import Path
from typing import Iterable, List, Optional

logger = logging.getLogger(__name__)

# Directories that are never pushed or zipped (caches, tooling, internal indexes)
_PUBLISH_SKIP_DIRS = frozenset({
    ".git", "__pycache__", "node_modules", ".pytest_cache", "htmlcov",
    ".tox", "venv", ".venv", "dist", "build", "target", ".idea",
    ".tldr", ".cursor",
})

# Platform/runtime internals — not user-facing project artifacts.
# Planning docs (user_stories.md, tech_stack.md, solution_spec.md, etc.) are kept.
_PUBLISH_EXCLUDE_NAMES = frozenset({
    "agent_backstories.json",
    "agent_prompts.json",
    "agents_prompt.json",
    "crew_errors.log",
    "import_index_manifest.json",
    "delivery_mode_triage.json",
    ".orchestrator_state.json",
    ".crew_state.json",
    "call_graph.json",
})

_PUBLISH_EXCLUDE_PATTERNS = (
    "state_*.json",
    "tasks_*.db",
    "*.db-shm",
    "*.db-wal",
    "repomix-output*.xml",
    "*.packed.xml",
    "*.plan.md",
)

_REPO_STOP_WORDS = frozenset({
    "a", "an", "the", "and", "or", "for", "to", "of", "in", "on", "with",
    "build", "create", "make", "develop", "implement", "simple", "basic",
    "new", "project", "app", "application", "tool", "using", "that", "this",
    "please", "want", "need", "help", "me", "my", "our", "your",
})


def should_exclude_from_publish(rel_path_str: str, name: str) -> bool:
    """Return True if this path should not be exported or pushed to GitHub."""
    if name in _PUBLISH_EXCLUDE_NAMES:
        return True
    for pattern in _PUBLISH_EXCLUDE_PATTERNS:
        if fnmatch.fnmatch(name, pattern):
            return True
    parts = rel_path_str.replace("\\", "/").split("/")
    if ".git" in parts:
        return True
    if any(part in _PUBLISH_SKIP_DIRS for part in parts):
        return True
    return False


def collect_publishable_files(publish_root: Path) -> List[str]:
    """Relative paths under *publish_root* to include in export or GitHub push."""
    publish_root = Path(publish_root).resolve()
    if not publish_root.is_dir():
        return []

    paths: List[str] = []
    for root, dirs, filenames in os.walk(publish_root):
        dirs[:] = [d for d in dirs if d not in _PUBLISH_SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            full = Path(root) / name
            try:
                rel = full.resolve().relative_to(publish_root)
            except ValueError:
                continue
            rel_str = str(rel).replace("\\", "/")
            if should_exclude_from_publish(rel_str, name):
                continue
            paths.append(rel_str)
    return sorted(paths)


def _sanitize_repo_slug(text: str, *, max_len: int = 30) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:max_len].rstrip("-")


def heuristic_github_repo_name(vision: str, job_id: str) -> str:
    """Derive a short repo name from vision text without calling an LLM."""
    text = (vision or "").strip()
    if not text:
        return f"crew-ai-{job_id[:8]}"

    words = re.findall(r"[a-zA-Z0-9]+", text.lower())
    meaningful = [w for w in words if w not in _REPO_STOP_WORDS and len(w) > 1]
    if not meaningful:
        meaningful = words[:4] or [job_id[:8]]

    core = "-".join(meaningful[:4])
    slug = _sanitize_repo_slug(core, max_len=28)
    if not slug:
        slug = job_id[:8]
    return f"crew-ai-{slug}"


def suggest_github_repo_name(
    vision: str,
    job_id: str,
    *,
    config: Optional[object] = None,
) -> str:
    """Suggest a GitHub repo name: optional LLM, heuristic fallback."""
    heuristic = heuristic_github_repo_name(vision, job_id)
    if config is None:
        return heuristic

    try:
        from llamaindex_crew.utils.llm_config import get_llm_for_agent

        llm = get_llm_for_agent("worker", config=config)
        prompt = (
            "Suggest a short GitHub repository name for this software project.\n"
            "Rules: 2-4 English words, lowercase, hyphen-separated, max 28 characters, "
            "no 'crew-ai' prefix, no trailing hyphen, descriptive but concise.\n"
            "Reply with ONLY the name.\n\n"
            f"Project description:\n{vision[:500]}"
        )
        resp = llm.complete(prompt)
        raw = (getattr(resp, "text", None) or str(resp)).strip().strip("`\"'")
        raw = raw.splitlines()[0].strip()
        raw = re.sub(r"^crew-ai-", "", raw, flags=re.IGNORECASE)
        slug = _sanitize_repo_slug(raw, max_len=28)
        if slug and len(slug) >= 3:
            return f"crew-ai-{slug}"
    except Exception as e:
        logger.debug("LLM repo name suggestion skipped: %s", e)

    return heuristic


def stage_publishable_files(repo, publish_root: Path, paths: Iterable[str]) -> int:
    """Clear the index and stage only *paths* relative to *publish_root*."""
    publish_root = Path(publish_root)
    path_list = list(paths)
    try:
        repo.git.rm("-r", "--cached", ".", "--ignore-unmatch")
    except Exception:
        pass

    staged = 0
    for rel in path_list:
        full = publish_root / rel
        if full.is_file():
            repo.index.add([rel])
            staged += 1
    return staged
