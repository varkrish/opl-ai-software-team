"""
Git operation tools for AI agents
Migrated from CrewAI BaseTool to LlamaIndex FunctionTool
"""
import os
from pathlib import Path
from llama_index.core.tools import FunctionTool
import git
from typing import Optional


def _is_git_enabled() -> bool:
    """Check if git operations are enabled"""
    git_enabled = os.getenv("ENABLE_GIT", "true").lower()
    return git_enabled in ("true", "1", "yes")


def git_init() -> str:
    """Initialize a git repository in the workspace.
    
    Returns:
        Success or error message
    """
    if not _is_git_enabled():
        return "ℹ️  Git operations are disabled (ENABLE_GIT=false). Skipping git init."
    try:
        workspace_path = os.getenv("WORKSPACE_PATH", "./workspace")
        workspace = Path(workspace_path)
        workspace.mkdir(parents=True, exist_ok=True)
        
        # Check if already a git repo
        if (workspace / ".git").exists():
            return "ℹ️  Git repository already initialized"
        
        # Initialize repo
        repo = git.Repo.init(workspace)
        
        # Create .gitignore
        gitignore_path = workspace / ".gitignore"
        if not gitignore_path.exists():
            with open(gitignore_path, 'w') as f:
                f.write("__pycache__/\n*.pyc\n.pytest_cache/\n.coverage\nhtmlcov/\n.env\n")
        
        return "✅ Git repository initialized successfully"
    except Exception as e:
        return f"❌ Error initializing git: {str(e)}"


def git_commit(message: str, files: Optional[str] = None) -> str:
    """Stage and commit changes to git. Provide a descriptive commit message. Files parameter can be a comma-separated string or will stage all changes if omitted.
    
    Args:
        message: Commit message
        files: Optional comma-separated list of files to commit, or None to commit all changes
    
    Returns:
        Success or error message
    """
    if not _is_git_enabled():
        return f"ℹ️  Git operations are disabled (ENABLE_GIT=false). Skipping commit: {message}"
    try:
        workspace_path = os.getenv("WORKSPACE_PATH", "./workspace")
        workspace = Path(workspace_path)
        repo = git.Repo(workspace)
        
        # Stage files
        if files:
            # Handle both string and potential list input
            if isinstance(files, list):
                file_list = files
            else:
                # Stage specific files from comma-separated string
                file_list = [f.strip() for f in files.split(',')]
            repo.index.add(file_list)
        else:
            # Stage all changes
            repo.git.add(A=True)
        
        # Check if there are staged changes
        if not repo.index.entries:
            return "ℹ️  No changes to commit (nothing staged)"
        
        # Commit
        commit = repo.index.commit(message)
        
        # Get stats (handle first commit case)
        try:
            if commit.parents:
                # Not first commit
                stats = repo.git.diff(commit.hexsha + "^", commit.hexsha, stat=True)
            else:
                # First commit
                stats = f"{len(list(repo.index.entries.keys()))} files added (initial commit)"
        except:
            stats = "Changes committed"
        
        return f"✅ Committed: {commit.hexsha[:7]} - {message}\n\n{stats}"
    except git.exc.InvalidGitRepositoryError:
        return "❌ Not a git repository. Run git_init first."
    except Exception as e:
        return f"❌ Error committing: {str(e)}"


def git_status() -> str:
    """Check git repository status. Shows modified, staged, and untracked files.
    
    Returns:
        Git status or error message
    """
    if not _is_git_enabled():
        return "ℹ️  Git operations are disabled (ENABLE_GIT=false). Skipping git status."
    try:
        workspace_path = os.getenv("WORKSPACE_PATH", "./workspace")
        workspace = Path(workspace_path)
        repo = git.Repo(workspace)
        
        # Get status
        status_lines = []
        
        # Modified files
        modified = [item.a_path for item in repo.index.diff(None)]
        if modified:
            status_lines.append("📝 Modified files:")
            for f in modified:
                status_lines.append(f"  - {f}")
        
        # Staged files
        staged = [item.a_path for item in repo.index.diff("HEAD")]
        if staged:
            status_lines.append("\n✅ Staged files:")
            for f in staged:
                status_lines.append(f"  - {f}")
        
        # Untracked files
        untracked = repo.untracked_files
        if untracked:
            status_lines.append("\n❓ Untracked files:")
            for f in untracked:
                status_lines.append(f"  - {f}")
        
        if not status_lines:
            return "✅ Working tree clean - no changes"
        
        return "\n".join(status_lines)
    except git.exc.InvalidGitRepositoryError:
        return "❌ Not a git repository. Run git_init first."
    except Exception as e:
        return f"❌ Error getting status: {str(e)}"


# Create FunctionTool instances
GitInitTool = FunctionTool.from_defaults(
    fn=git_init,
    name="git_init",
    description="Initialize a git repository in the workspace."
)

GitCommitTool = FunctionTool.from_defaults(
    fn=git_commit,
    name="git_commit",
    description="Stage and commit changes to git. Provide a descriptive commit message. Files parameter can be a comma-separated string or will stage all changes if omitted."
)

GitStatusTool = FunctionTool.from_defaults(
    fn=git_status,
    name="git_status",
    description="Check git repository status. Shows modified, staged, and untracked files."
)
