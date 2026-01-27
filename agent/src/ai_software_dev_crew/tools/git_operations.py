"""
Git operation tools for AI agents
"""
import os
from pathlib import Path
from crewai.tools import BaseTool
from pydantic import Field
import git
from typing import Optional


def _is_git_enabled() -> bool:
    """Check if git operations are enabled"""
    # Default to True for production, False for local testing
    git_enabled = os.getenv("ENABLE_GIT", "true").lower()
    return git_enabled in ("true", "1", "yes")


class GitInitTool(BaseTool):
    name: str = "git_init"
    description: str = "Initialize a git repository in the workspace."
    
    workspace_path: str = Field(default_factory=lambda: os.getenv("WORKSPACE_PATH", "./workspace"))
    
    def _run(self) -> str:
        """Initialize git repository"""
        if not _is_git_enabled():
            return "ℹ️  Git operations are disabled (ENABLE_GIT=false). Skipping git init."
        try:
            workspace = Path(self.workspace_path)
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


class GitCommitTool(BaseTool):
    name: str = "git_commit"
    description: str = "Stage and commit changes to git. Provide a descriptive commit message. Files parameter can be a comma-separated string or will stage all changes if omitted."
    
    workspace_path: str = Field(default_factory=lambda: os.getenv("WORKSPACE_PATH", "./workspace"))
    
    def _run(self, message: str, files: Optional[str] = None) -> str:
        """Commit changes to git"""
        if not _is_git_enabled():
            return f"ℹ️  Git operations are disabled (ENABLE_GIT=false). Skipping commit: {message}"
        try:
            workspace = Path(self.workspace_path)
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


class GitStatusTool(BaseTool):
    name: str = "git_status"
    description: str = "Check git repository status. Shows modified, staged, and untracked files."
    
    workspace_path: str = Field(default_factory=lambda: os.getenv("WORKSPACE_PATH", "./workspace"))
    
    def _run(self) -> str:
        """Get git status"""
        if not _is_git_enabled():
            return "ℹ️  Git operations are disabled (ENABLE_GIT=false). Skipping git status."
        try:
            workspace = Path(self.workspace_path)
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


class GitLogTool(BaseTool):
    name: str = "git_log"
    description: str = "Show recent git commit history."
    
    workspace_path: str = Field(default_factory=lambda: os.getenv("WORKSPACE_PATH", "./workspace"))
    
    def _run(self, max_count: int = 10) -> str:
        """Get git log"""
        if not _is_git_enabled():
            return "ℹ️  Git operations are disabled (ENABLE_GIT=false). Skipping git log."
        try:
            workspace = Path(self.workspace_path)
            repo = git.Repo(workspace)
            
            commits = list(repo.iter_commits('HEAD', max_count=max_count))
            
            if not commits:
                return "ℹ️  No commits yet"
            
            log_lines = ["📜 Recent commits:\n"]
            for commit in commits:
                log_lines.append(f"  {commit.hexsha[:7]} - {commit.author.name}")
                log_lines.append(f"  {commit.committed_datetime.strftime('%Y-%m-%d %H:%M')}")
                log_lines.append(f"  {commit.message.strip()}\n")
            
            return "\n".join(log_lines)
        except git.exc.InvalidGitRepositoryError:
            return "❌ Not a git repository. Run git_init first."
        except Exception as e:
            return f"❌ Error getting log: {str(e)}"

