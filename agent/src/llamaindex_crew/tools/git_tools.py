"""
Unified git tool for AI agents.

Provides a single `git(command)` function that dispatches to subcommands:
  init, clone, status, commit, push, pull, log, branch, checkout, diff

Agents call this like: git("clone https://github.com/owner/repo")
"""

import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional

import git as gitmodule
from llama_index.core.tools import FunctionTool

from .file_tools import _resolve_workspace


def _is_git_enabled() -> bool:
    return os.getenv("ENABLE_GIT", "true").lower() in ("true", "1", "yes")


def _resolve_ws() -> Path:
    return _resolve_workspace()


# ---------------------------------------------------------------------------
# Subcommand implementations
# ---------------------------------------------------------------------------

def _git_init(workspace: Path) -> str:
    workspace.mkdir(parents=True, exist_ok=True)
    if (workspace / ".git").is_dir():
        return "ℹ️  Git repository already initialized"
    gitmodule.Repo.init(workspace)
    gitignore = workspace / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(
            "__pycache__/\n*.pyc\n.pytest_cache/\n.coverage\nhtmlcov/\n.env\n"
        )
    return "✅ Git repository initialized successfully"


def clone_repository_into_directory(url: str, target_dir: Path, *, keep_git: bool = True) -> str:
    """Clone a remote repository URL into ``target_dir``.

    When *keep_git* is True (default), the ``.git`` directory and full history
    are preserved so branches, pushes, and PRs work later.  When False, only
    the working-tree files are copied (legacy behavior for agent sandboxes).

    Used by the agent ``git`` tool, HTTP clone helpers, and tests. Returns the
    same user-facing status string as ``git('clone …')``.
    """
    url = url.strip()
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    try:
        if keep_git:
            # Clone into a temp subdir, then move contents into target_dir.
            # git clone requires the destination to not exist yet.
            with tempfile.TemporaryDirectory() as tmp:
                clone_path = Path(tmp) / "repo"
                repo = gitmodule.Repo.clone_from(url, str(clone_path), depth=1)
                # Unshallow so branches share history with the remote
                try:
                    repo.git.fetch("--unshallow")
                except gitmodule.exc.GitCommandError:
                    pass  # already full or remote doesn't support it

                # Move everything (including .git) into target_dir
                for item in clone_path.iterdir():
                    shutil.move(str(item), str(target_dir / item.name))

            file_count = sum(1 for f in target_dir.rglob("*")
                             if f.is_file() and ".git" not in f.parts)
            return f"✅ Cloned {url} — {file_count} files in workspace (git history preserved)"

        # Legacy: files-only (no .git) for agent sandboxes
        with tempfile.TemporaryDirectory() as tmp:
            clone_path = Path(tmp) / "clone"
            gitmodule.Repo.clone_from(url, str(clone_path), depth=1)

            contents = list(clone_path.iterdir())
            non_git = [c for c in contents if c.name != ".git"]

            source_dir = clone_path
            if len(non_git) == 1 and non_git[0].is_dir():
                source_dir = non_git[0]

            file_count = 0
            for item in source_dir.rglob("*"):
                if item.is_file() and ".git" not in item.parts:
                    rel = item.relative_to(source_dir)
                    dest = target_dir / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, dest)
                    file_count += 1

        return f"✅ Cloned {url} — {file_count} files copied to workspace"
    except gitmodule.exc.GitCommandError as e:
        return f"❌ Clone failed: {e.stderr.strip() if e.stderr else str(e)}"
    except Exception as e:
        return f"❌ Clone error: {str(e)}"


def _git_clone(args: str, workspace: Path) -> str:
    parts = args.strip().split()
    if not parts:
        return "❌ Usage: git clone <url> [target_subdir]"
    url = parts[0]
    subdir = parts[1] if len(parts) > 1 else ""

    target = workspace / subdir if subdir else workspace
    return clone_repository_into_directory(url, target, keep_git=False)


def _git_status(workspace: Path) -> str:
    repo = gitmodule.Repo(workspace)
    lines = []

    modified = [item.a_path for item in repo.index.diff(None)]
    if modified:
        lines.append("📝 Modified files:")
        lines.extend(f"  - {f}" for f in modified)

    try:
        staged = [item.a_path for item in repo.index.diff("HEAD")]
        if staged:
            lines.append("\n✅ Staged files:")
            lines.extend(f"  - {f}" for f in staged)
    except gitmodule.exc.BadName:
        pass

    untracked = repo.untracked_files
    if untracked:
        lines.append("\n❓ Untracked files:")
        lines.extend(f"  - {f}" for f in untracked)

    if not lines:
        return "✅ Working tree clean - no changes"
    return "\n".join(lines)


def _repo_has_head_commit(repo: gitmodule.Repo) -> bool:
    try:
        _ = repo.head.commit
        return True
    except (ValueError, gitmodule.exc.BadName):
        return False


def _git_commit(args: str, workspace: Path) -> str:
    repo = gitmodule.Repo(workspace)

    files_flag = None
    message = args
    if "--files=" in args:
        idx = args.index("--files=")
        message = args[:idx].strip()
        files_flag = args[idx + 8:].strip()

    if not message:
        message = "auto-commit"

    if files_flag:
        file_list = [f.strip() for f in files_flag.split(",")]
        repo.index.add(file_list)
    else:
        repo.git.add(A=True)

    if not repo.is_dirty(index=True, untracked_files=True):
        return "ℹ️  No changes to commit"

    if _repo_has_head_commit(repo):
        if not repo.index.diff("HEAD") and not repo.untracked_files:
            try:
                if not repo.index.diff(None) and not repo.untracked_files:
                    return "ℹ️  No changes to commit"
            except gitmodule.exc.BadName:
                pass

    commit = repo.index.commit(message)
    try:
        if commit.parents:
            stats = repo.git.diff(f"{commit.hexsha}^", commit.hexsha, stat=True)
        else:
            stats = f"{len(list(repo.tree().traverse()))} files (initial commit)"
    except Exception:
        stats = "Changes committed"
    return f"✅ Committed: {commit.hexsha[:7]} - {message}\n\n{stats}"


def _git_log(args: str, workspace: Path) -> str:
    repo = gitmodule.Repo(workspace)
    max_count = 10
    if args.strip().isdigit():
        max_count = int(args.strip())

    try:
        commits = list(repo.iter_commits("HEAD", max_count=max_count))
    except gitmodule.exc.GitCommandError:
        return "ℹ️  No commits yet"

    if not commits:
        return "ℹ️  No commits yet"

    lines = ["📜 Recent commits:\n"]
    for c in commits:
        lines.append(f"  {c.hexsha[:7]} {c.message.strip()}")
    return "\n".join(lines)


def _git_branch(args: str, workspace: Path) -> str:
    repo = gitmodule.Repo(workspace)
    name = args.strip()

    if not name:
        branches = [h.name for h in repo.heads]
        active = repo.active_branch.name if not repo.head.is_detached else "(detached)"
        lines = []
        for b in branches:
            prefix = "* " if b == active else "  "
            lines.append(f"{prefix}{b}")
        return "\n".join(lines) if lines else "ℹ️  No branches yet"

    if name in [h.name for h in repo.heads]:
        return f"❌ Branch '{name}' already exists"
    repo.create_head(name)
    return f"✅ Created branch '{name}'"


def _git_checkout(args: str, workspace: Path) -> str:
    repo = gitmodule.Repo(workspace)
    branch_name = args.strip()
    if not branch_name:
        return "❌ Usage: git checkout <branch>"

    existing = [h.name for h in repo.heads]
    if branch_name not in existing:
        return f"❌ Branch '{branch_name}' not found. Available: {', '.join(existing)}"
    repo.heads[branch_name].checkout()
    return f"✅ Switched to branch '{branch_name}'"


def _git_diff(args: str, workspace: Path) -> str:
    repo = gitmodule.Repo(workspace)
    file_path = args.strip() or None

    if file_path:
        diff_output = repo.git.diff(file_path)
    else:
        diff_output = repo.git.diff()

    if not diff_output.strip():
        return "ℹ️  No changes (diff is empty)"
    return diff_output


def _git_push(args: str, workspace: Path) -> str:
    repo = gitmodule.Repo(workspace)
    parts = args.strip().split()
    remote_name = parts[0] if parts else "origin"
    branch_name = parts[1] if len(parts) > 1 else None

    if not repo.remotes:
        return "❌ No remote configured. Add a remote first."

    try:
        remote = repo.remote(remote_name)
    except ValueError:
        return f"❌ Remote '{remote_name}' not found. Available: {', '.join(r.name for r in repo.remotes)}"

    try:
        if branch_name:
            info = remote.push(f"HEAD:refs/heads/{branch_name}")
        else:
            info = remote.push()
        summaries = [str(pi.summary) for pi in info]
        return f"✅ Pushed to {remote_name}. {'; '.join(summaries)}"
    except gitmodule.exc.GitCommandError as e:
        return f"❌ Push failed: {e.stderr.strip() if e.stderr else str(e)}"


def _git_pull(args: str, workspace: Path) -> str:
    repo = gitmodule.Repo(workspace)
    parts = args.strip().split()
    remote_name = parts[0] if parts else "origin"

    if not repo.remotes:
        return "❌ No remote configured. Add a remote first."

    try:
        remote = repo.remote(remote_name)
    except ValueError:
        return f"❌ Remote '{remote_name}' not found."

    try:
        info = remote.pull()
        if not info:
            return "✅ Already up to date"
        return f"✅ Pulled from {remote_name}"
    except gitmodule.exc.GitCommandError as e:
        return f"❌ Pull failed: {e.stderr.strip() if e.stderr else str(e)}"


# ---------------------------------------------------------------------------
# Main dispatch function
# ---------------------------------------------------------------------------

_COMMANDS = {
    "init", "clone", "status", "commit", "push", "pull",
    "log", "branch", "checkout", "diff",
}


def git(command: str) -> str:
    """Execute a git operation in the workspace.

    Supported commands:
      clone <url> [subdir]   - Clone a repository into the workspace
      init                   - Initialize a new git repository
      status                 - Show modified, staged, and untracked files
      commit <message>       - Stage all and commit (use --files=a,b to limit)
      push [remote] [branch] - Push commits to a remote
      pull [remote]          - Pull from a remote
      log [n]                - Show last n commits (default 10)
      branch [name]          - List branches or create one
      checkout <branch>      - Switch to an existing branch
      diff [file]            - Show unstaged changes

    Args:
        command: The git subcommand and its arguments as a single string.

    Returns:
        Result message (success or error).
    """
    if not command or not command.strip():
        return f"❌ No command provided. Supported: {', '.join(sorted(_COMMANDS))}"

    parts = command.strip().split(None, 1)
    subcmd = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""

    if subcmd not in _COMMANDS:
        return f"❌ Unknown git command '{subcmd}'. Supported: {', '.join(sorted(_COMMANDS))}"

    if not _is_git_enabled():
        return f"ℹ️  Git operations are disabled (ENABLE_GIT=false). Skipping git {subcmd}."

    workspace = _resolve_ws()

    if subcmd == "init":
        return _git_init(workspace)
    if subcmd == "clone":
        return _git_clone(args, workspace)

    # All other commands require an existing repo
    if subcmd != "init" and subcmd != "clone":
        if not (workspace / ".git").is_dir():
            return "❌ Not a git repository. Run git init first."

    try:
        if subcmd == "status":
            return _git_status(workspace)
        elif subcmd == "commit":
            return _git_commit(args, workspace)
        elif subcmd == "log":
            return _git_log(args, workspace)
        elif subcmd == "branch":
            return _git_branch(args, workspace)
        elif subcmd == "checkout":
            return _git_checkout(args, workspace)
        elif subcmd == "diff":
            return _git_diff(args, workspace)
        elif subcmd == "push":
            return _git_push(args, workspace)
        elif subcmd == "pull":
            return _git_pull(args, workspace)
    except gitmodule.exc.InvalidGitRepositoryError:
        return "❌ Not a git repository. Run git init first."
    except Exception as e:
        return f"❌ git {subcmd} error: {str(e)}"

    return f"❌ Unhandled command: {subcmd}"


# ---------------------------------------------------------------------------
# Backward-compatible entrypoints (CrewAI-style function names)
# ---------------------------------------------------------------------------


def git_init() -> str:
    """Initialize a git repository in the workspace."""
    return git("init")


def git_commit(message: str, files: Optional[str] = None) -> str:
    """Stage and commit changes. ``files`` may be a comma-separated string or list of paths."""
    if files:
        if isinstance(files, list):
            files = ",".join(str(x).strip() for x in files if str(x).strip())
        return git(f"commit {message} --files={files}")
    return git(f"commit {message}")


def git_status() -> str:
    """Check git repository status."""
    return git("status")


# ---------------------------------------------------------------------------
# FunctionTool instances
# ---------------------------------------------------------------------------

GitTool = FunctionTool.from_defaults(
    fn=git,
    name="git",
    description=(
        "Execute git operations in the workspace. Pass a command string like "
        "'clone <url>', 'init', 'status', 'commit <msg>', 'push', 'pull', "
        "'log [n]', 'branch [name]', 'checkout <branch>', 'diff [file]'."
    ),
)

# Backward-compatible aliases
GitInitTool = GitTool
GitCommitTool = GitTool
GitStatusTool = GitTool
