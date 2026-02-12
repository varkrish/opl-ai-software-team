"""
Shared helpers for the migration module.
Decoupled from refinement_runner — same patterns, independent code.
"""
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def git_snapshot(workspace_path: Path, message: str = "pre-migration snapshot") -> str:
    """Create a git commit in the workspace. Returns status message."""
    try:
        import git
    except ImportError:
        logger.warning("gitpython not installed; skipping snapshot")
        return "Git snapshot skipped (git not available)"

    if os.getenv("ENABLE_GIT", "true").lower() not in ("true", "1", "yes"):
        return "Git snapshot skipped (ENABLE_GIT=false)"

    workspace_path = Path(workspace_path)
    try:
        if not (workspace_path / ".git").exists():
            repo = git.Repo.init(workspace_path)
            gitignore = workspace_path / ".gitignore"
            if not gitignore.exists():
                gitignore.write_text(
                    "__pycache__/\n*.pyc\n.pytest_cache/\n.coverage\nhtmlcov/\n.env\n",
                    encoding="utf-8",
                )
            repo.index.add([".gitignore"])
        else:
            repo = git.Repo(workspace_path)

        repo.git.add(A=True)
        if repo.is_dirty(untracked_files=True) or repo.untracked_files:
            commit = repo.index.commit(message)
            return f"Git snapshot created: {commit.hexsha[:7]}"
        return "No changes to snapshot (working tree clean)"
    except Exception as e:
        logger.warning("Git snapshot failed: %s", e)
        return f"Git snapshot skipped: {e}"


def workspace_has_changes(workspace_path: Path) -> bool:
    """Check if the workspace has uncommitted changes."""
    try:
        import git
    except ImportError:
        return True  # Can't verify; assume changes
    workspace_path = Path(workspace_path)
    try:
        if not (workspace_path / ".git").exists():
            return True
        repo = git.Repo(workspace_path)
        return repo.is_dirty(untracked_files=True) or bool(repo.untracked_files)
    except Exception:
        return True


# Convention file names for Tier 2 injection
MIGRATION_RULES_FILES = (".migration-rules.md", "MIGRATION.md", "migration-rules.md")


def load_migration_rules(workspace_path: Path) -> str | None:
    """Load Tier 2 convention rules from the workspace root. Returns content or None."""
    for filename in MIGRATION_RULES_FILES:
        rules_path = workspace_path / filename
        if rules_path.is_file():
            try:
                content = rules_path.read_text(encoding="utf-8")[:50_000]
                logger.info("Loaded migration rules from %s", rules_path)
                return content
            except Exception as e:
                logger.warning("Failed to read %s: %s", rules_path, e)
    return None
