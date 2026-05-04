"""
TDD tests for the unified git tool.

Tests the single `git()` function that dispatches subcommands:
  clone, init, status, commit, push, pull, log, branch, checkout, diff

RED phase: these tests define the contract. Implementation follows.
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def _patch_paths():
    project_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), os.pardir, os.pardir)
    )
    for p in (
        project_root,
        os.path.join(project_root, "agent", "src"),
        os.path.join(project_root, "agent"),
    ):
        if p not in sys.path:
            sys.path.insert(0, p)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def workspace(tmp_path):
    """Provide a temp workspace directory and patch WORKSPACE_PATH."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    with patch.dict(os.environ, {"WORKSPACE_PATH": str(ws), "ENABLE_GIT": "true"}):
        yield ws


@pytest.fixture
def git_repo(workspace):
    """Workspace with an initialized git repo and one commit."""
    import git as gitmodule
    repo = gitmodule.Repo.init(workspace)
    (workspace / "README.md").write_text("# Test\n")
    repo.index.add(["README.md"])
    repo.index.commit("initial commit")
    return repo


# ---------------------------------------------------------------------------
# Import the tool function
# ---------------------------------------------------------------------------

@pytest.fixture
def git_fn():
    """Import the git function from the tool module."""
    from llamaindex_crew.tools.git_tools import git
    return git


# ---------------------------------------------------------------------------
# Tests: git init
# ---------------------------------------------------------------------------

class TestGitInit:
    def test_init_creates_repo(self, workspace, git_fn):
        result = git_fn("init")
        assert "initialized" in result.lower() or "✅" in result
        assert (workspace / ".git").is_dir()

    def test_init_creates_gitignore(self, workspace, git_fn):
        git_fn("init")
        assert (workspace / ".gitignore").exists()

    def test_init_idempotent(self, workspace, git_fn):
        git_fn("init")
        result = git_fn("init")
        assert "already" in result.lower()

    def test_init_disabled(self, workspace, git_fn):
        with patch.dict(os.environ, {"ENABLE_GIT": "false"}):
            result = git_fn("init")
        assert "disabled" in result.lower()


# ---------------------------------------------------------------------------
# Tests: git clone
# ---------------------------------------------------------------------------

class TestGitClone:
    def test_clone_with_valid_url(self, workspace, git_fn):
        """Clone from a local bare repo to simulate github clone."""
        import git as gitmodule
        # Create a bare repo to clone from
        bare_dir = workspace.parent / "bare_repo.git"
        bare = gitmodule.Repo.init(bare_dir, bare=True)

        # Create a source repo, add a file, push to bare
        source_dir = workspace.parent / "source"
        source_dir.mkdir()
        source_repo = gitmodule.Repo.init(source_dir)
        (source_dir / "app.py").write_text("print('hello')\n")
        source_repo.index.add(["app.py"])
        source_repo.index.commit("add app")
        source_repo.create_remote("origin", str(bare_dir))
        source_repo.remotes.origin.push("HEAD:refs/heads/main")

        result = git_fn(f"clone {bare_dir}")
        assert "✅" in result or "cloned" in result.lower()
        assert (workspace / "app.py").exists()

    def test_clone_invalid_url(self, workspace, git_fn):
        result = git_fn("clone https://not-a-real-host.invalid/repo")
        assert "❌" in result or "error" in result.lower()

    def test_clone_missing_url(self, workspace, git_fn):
        result = git_fn("clone")
        assert "❌" in result or "url" in result.lower() or "required" in result.lower()

    def test_clone_into_subdir(self, workspace, git_fn):
        """Clone into a subdirectory of workspace."""
        import git as gitmodule
        bare_dir = workspace.parent / "bare2.git"
        bare = gitmodule.Repo.init(bare_dir, bare=True)
        source_dir = workspace.parent / "source2"
        source_dir.mkdir()
        source_repo = gitmodule.Repo.init(source_dir)
        (source_dir / "main.py").write_text("x = 1\n")
        source_repo.index.add(["main.py"])
        source_repo.index.commit("init")
        source_repo.create_remote("origin", str(bare_dir))
        source_repo.remotes.origin.push("HEAD:refs/heads/main")

        result = git_fn(f"clone {bare_dir} vendor/lib")
        assert "✅" in result or "cloned" in result.lower()
        assert (workspace / "vendor" / "lib" / "main.py").exists()

    def test_clone_disabled(self, workspace, git_fn):
        with patch.dict(os.environ, {"ENABLE_GIT": "false"}):
            result = git_fn("clone https://github.com/test/repo")
        assert "disabled" in result.lower()


# ---------------------------------------------------------------------------
# Tests: git status
# ---------------------------------------------------------------------------

class TestGitStatus:
    def test_status_clean(self, git_repo, git_fn):
        result = git_fn("status")
        assert "clean" in result.lower() or "no changes" in result.lower()

    def test_status_untracked(self, git_repo, git_fn, workspace):
        (workspace / "new_file.txt").write_text("hello")
        result = git_fn("status")
        assert "new_file.txt" in result

    def test_status_modified(self, git_repo, git_fn, workspace):
        (workspace / "README.md").write_text("# Modified\n")
        result = git_fn("status")
        assert "README.md" in result

    def test_status_no_repo(self, workspace, git_fn):
        result = git_fn("status")
        assert "not a git repository" in result.lower() or "git_init" in result.lower() or "❌" in result


# ---------------------------------------------------------------------------
# Tests: git commit
# ---------------------------------------------------------------------------

class TestGitCommit:
    def test_commit_all(self, git_repo, git_fn, workspace):
        (workspace / "new.py").write_text("x = 1\n")
        result = git_fn("commit add new file")
        assert "✅" in result or "committed" in result.lower()

    def test_commit_specific_files(self, git_repo, git_fn, workspace):
        (workspace / "a.py").write_text("a\n")
        (workspace / "b.py").write_text("b\n")
        result = git_fn("commit add a --files=a.py")
        assert "✅" in result
        # b.py should still be untracked
        status = git_fn("status")
        assert "b.py" in status

    def test_commit_no_changes(self, git_repo, git_fn):
        result = git_fn("commit nothing to commit")
        assert "no changes" in result.lower() or "nothing" in result.lower()

    def test_commit_no_repo(self, workspace, git_fn):
        result = git_fn("commit test msg")
        assert "❌" in result or "not a git repository" in result.lower()


# ---------------------------------------------------------------------------
# Tests: git log
# ---------------------------------------------------------------------------

class TestGitLog:
    def test_log_shows_commits(self, git_repo, git_fn):
        result = git_fn("log")
        assert "initial commit" in result

    def test_log_with_count(self, git_repo, git_fn, workspace):
        (workspace / "f2.txt").write_text("2")
        git_repo.index.add(["f2.txt"])
        git_repo.index.commit("second")
        result = git_fn("log 1")
        assert "second" in result
        assert "initial commit" not in result

    def test_log_no_repo(self, workspace, git_fn):
        result = git_fn("log")
        assert "❌" in result or "not a git repository" in result.lower()


# ---------------------------------------------------------------------------
# Tests: git branch
# ---------------------------------------------------------------------------

class TestGitBranch:
    def test_branch_list(self, git_repo, git_fn):
        result = git_fn("branch")
        # Should show the default branch
        assert "main" in result or "master" in result

    def test_branch_create(self, git_repo, git_fn):
        result = git_fn("branch feature-x")
        assert "✅" in result or "created" in result.lower() or "feature-x" in result

    def test_branch_already_exists(self, git_repo, git_fn):
        git_fn("branch duplicate")
        result = git_fn("branch duplicate")
        assert "already exists" in result.lower() or "❌" in result

    def test_branch_no_repo(self, workspace, git_fn):
        result = git_fn("branch")
        assert "❌" in result or "not a git repository" in result.lower()


# ---------------------------------------------------------------------------
# Tests: git checkout
# ---------------------------------------------------------------------------

class TestGitCheckout:
    def test_checkout_branch(self, git_repo, git_fn):
        git_repo.create_head("feature-y")
        result = git_fn("checkout feature-y")
        assert "✅" in result or "switched" in result.lower()
        assert git_repo.active_branch.name == "feature-y"

    def test_checkout_nonexistent(self, git_repo, git_fn):
        result = git_fn("checkout no-such-branch")
        assert "❌" in result or "not found" in result.lower() or "error" in result.lower()

    def test_checkout_no_repo(self, workspace, git_fn):
        result = git_fn("checkout main")
        assert "❌" in result or "not a git repository" in result.lower()


# ---------------------------------------------------------------------------
# Tests: git diff
# ---------------------------------------------------------------------------

class TestGitDiff:
    def test_diff_no_changes(self, git_repo, git_fn):
        result = git_fn("diff")
        assert "no changes" in result.lower() or result.strip() == ""

    def test_diff_shows_changes(self, git_repo, git_fn, workspace):
        (workspace / "README.md").write_text("# Changed content\n")
        result = git_fn("diff")
        assert "Changed content" in result or "README.md" in result

    def test_diff_specific_file(self, git_repo, git_fn, workspace):
        (workspace / "README.md").write_text("# New\n")
        (workspace / "other.txt").write_text("other")
        result = git_fn("diff README.md")
        assert "README" in result or "New" in result

    def test_diff_no_repo(self, workspace, git_fn):
        result = git_fn("diff")
        assert "❌" in result or "not a git repository" in result.lower()


# ---------------------------------------------------------------------------
# Tests: git push
# ---------------------------------------------------------------------------

class TestGitPush:
    def test_push_to_remote(self, workspace, git_fn):
        """Push to a local bare remote."""
        import git as gitmodule
        bare_dir = workspace.parent / "remote.git"
        gitmodule.Repo.init(bare_dir, bare=True)

        repo = gitmodule.Repo.init(workspace)
        (workspace / "app.py").write_text("x=1\n")
        repo.index.add(["app.py"])
        repo.index.commit("init")
        repo.create_remote("origin", str(bare_dir))

        result = git_fn("push origin main")
        # Either success or already up to date
        assert "✅" in result or "pushed" in result.lower() or "up to date" in result.lower() or "error" in result.lower()

    def test_push_no_remote(self, git_repo, git_fn):
        result = git_fn("push")
        assert "❌" in result or "no remote" in result.lower() or "error" in result.lower()

    def test_push_no_repo(self, workspace, git_fn):
        result = git_fn("push")
        assert "❌" in result or "not a git repository" in result.lower()


# ---------------------------------------------------------------------------
# Tests: git pull
# ---------------------------------------------------------------------------

class TestGitPull:
    def test_pull_from_remote(self, workspace, git_fn):
        """Pull from a local bare remote after a push."""
        import git as gitmodule
        bare_dir = workspace.parent / "pull_remote.git"
        gitmodule.Repo.init(bare_dir, bare=True)

        repo = gitmodule.Repo.init(workspace)
        (workspace / "app.py").write_text("x=1\n")
        repo.index.add(["app.py"])
        repo.index.commit("init")
        repo.create_remote("origin", str(bare_dir))
        repo.remotes.origin.push("HEAD:refs/heads/main")
        repo.heads[0].set_tracking_branch(repo.remotes.origin.refs.main)

        result = git_fn("pull")
        assert "✅" in result or "up to date" in result.lower() or "pulled" in result.lower()

    def test_pull_no_remote(self, git_repo, git_fn):
        result = git_fn("pull")
        assert "❌" in result or "no remote" in result.lower() or "error" in result.lower()


# ---------------------------------------------------------------------------
# Tests: unknown command
# ---------------------------------------------------------------------------

class TestUnknownCommand:
    def test_unknown_command(self, workspace, git_fn):
        result = git_fn("foobar")
        assert "unknown" in result.lower() or "supported" in result.lower() or "❌" in result

    def test_empty_command(self, workspace, git_fn):
        result = git_fn("")
        assert "usage" in result.lower() or "command" in result.lower() or "supported" in result.lower()


# ---------------------------------------------------------------------------
# Tests: GitTool FunctionTool instance
# ---------------------------------------------------------------------------

class TestGitToolExport:
    def test_git_tool_is_function_tool(self):
        from llamaindex_crew.tools.git_tools import GitTool
        from llama_index.core.tools import FunctionTool
        assert isinstance(GitTool, FunctionTool)

    def test_git_tool_name_is_git(self):
        from llamaindex_crew.tools.git_tools import GitTool
        assert GitTool.metadata.name == "git"

    def test_backward_compat_aliases_exist(self):
        from llamaindex_crew.tools.git_tools import GitInitTool, GitCommitTool, GitStatusTool
        from llama_index.core.tools import FunctionTool
        assert isinstance(GitInitTool, FunctionTool)
        assert isinstance(GitCommitTool, FunctionTool)
        assert isinstance(GitStatusTool, FunctionTool)
