"""
Comprehensive tests for thread-local workspace isolation.

Verifies that concurrent jobs write to their own workspace directories
and never cross-contaminate each other, even under high concurrency.
"""
import os
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from llamaindex_crew.tools.file_tools import (
    _resolve_workspace,
    _workspace_local,
    clear_thread_workspace,
    file_lister,
    file_reader,
    file_writer,
    set_thread_workspace,
)


# ---------------------------------------------------------------------------
# set_thread_workspace / clear_thread_workspace / _resolve_workspace
# ---------------------------------------------------------------------------

class TestResolveWorkspacePriority:
    """_resolve_workspace must honour: explicit arg > thread-local > env var."""

    def test_explicit_arg_wins_over_everything(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKSPACE_PATH", "/env/workspace")
        set_thread_workspace("/thread/workspace")
        try:
            assert _resolve_workspace(str(tmp_path)) == tmp_path
        finally:
            clear_thread_workspace()

    def test_thread_local_wins_over_env(self, monkeypatch):
        monkeypatch.setenv("WORKSPACE_PATH", "/env/workspace")
        set_thread_workspace("/thread/workspace")
        try:
            assert _resolve_workspace() == Path("/thread/workspace")
        finally:
            clear_thread_workspace()

    def test_env_var_used_when_no_thread_local(self, monkeypatch):
        clear_thread_workspace()
        monkeypatch.setenv("WORKSPACE_PATH", "/env/workspace")
        assert _resolve_workspace() == Path("/env/workspace")

    def test_default_fallback_when_nothing_set(self, monkeypatch):
        clear_thread_workspace()
        monkeypatch.delenv("WORKSPACE_PATH", raising=False)
        assert _resolve_workspace() == Path("./workspace")


class TestSetClearThreadWorkspace:
    """set_thread_workspace / clear_thread_workspace lifecycle."""

    def test_set_and_read(self):
        set_thread_workspace("/my/ws")
        try:
            assert getattr(_workspace_local, "workspace_path", None) == "/my/ws"
        finally:
            clear_thread_workspace()

    def test_clear_resets_to_none(self):
        set_thread_workspace("/my/ws")
        clear_thread_workspace()
        assert getattr(_workspace_local, "workspace_path", None) is None

    def test_overwrite(self):
        set_thread_workspace("/first")
        set_thread_workspace("/second")
        try:
            assert _resolve_workspace() == Path("/second")
        finally:
            clear_thread_workspace()


# ---------------------------------------------------------------------------
# Thread isolation — the core correctness guarantee
# ---------------------------------------------------------------------------

class TestThreadIsolation:
    """Each thread must see only its own workspace, regardless of timing."""

    def test_two_threads_see_own_workspaces(self, tmp_path):
        ws_a = tmp_path / "job-A"
        ws_b = tmp_path / "job-B"
        ws_a.mkdir()
        ws_b.mkdir()

        seen = {}
        barrier = threading.Barrier(2)

        def worker(name, ws):
            set_thread_workspace(str(ws))
            barrier.wait()          # force both threads to race
            seen[name] = _resolve_workspace()
            clear_thread_workspace()

        t_a = threading.Thread(target=worker, args=("A", ws_a))
        t_b = threading.Thread(target=worker, args=("B", ws_b))
        t_a.start(); t_b.start()
        t_a.join(); t_b.join()

        assert seen["A"] == ws_a
        assert seen["B"] == ws_b

    def test_many_threads_no_cross_contamination(self, tmp_path):
        """Stress test: N threads each write a file, verify it lands in their workspace."""
        n_threads = 20
        workspaces = []
        for i in range(n_threads):
            ws = tmp_path / f"job-{i}"
            ws.mkdir()
            workspaces.append(ws)

        errors = []
        barrier = threading.Barrier(n_threads)

        def worker(idx, ws):
            set_thread_workspace(str(ws))
            barrier.wait()
            result = file_writer(f"output_{idx}.txt", f"thread-{idx}")
            resolved = _resolve_workspace()
            if resolved != ws:
                errors.append(f"Thread {idx}: expected {ws}, got {resolved}")
            written = ws / f"output_{idx}.txt"
            if not written.exists():
                errors.append(f"Thread {idx}: file not found at {written}")
            elif written.read_text() != f"thread-{idx}":
                errors.append(f"Thread {idx}: wrong content in {written}")
            clear_thread_workspace()

        threads = [
            threading.Thread(target=worker, args=(i, workspaces[i]))
            for i in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Cross-contamination detected:\n" + "\n".join(errors)

    def test_main_thread_unaffected_by_child(self, tmp_path):
        ws_main = tmp_path / "main"
        ws_child = tmp_path / "child"
        ws_main.mkdir()
        ws_child.mkdir()

        set_thread_workspace(str(ws_main))

        def child():
            set_thread_workspace(str(ws_child))
            time.sleep(0.05)
            clear_thread_workspace()

        t = threading.Thread(target=child)
        t.start()
        t.join()

        assert _resolve_workspace() == ws_main
        clear_thread_workspace()


# ---------------------------------------------------------------------------
# file_writer / file_reader / file_lister use thread-local workspace
# ---------------------------------------------------------------------------

class TestFileToolsUseThreadLocal:
    """file_writer, file_reader, file_lister resolve via _resolve_workspace."""

    def test_file_writer_uses_thread_workspace(self, tmp_path):
        ws = tmp_path / "job-write"
        ws.mkdir()
        set_thread_workspace(str(ws))
        try:
            result = file_writer("hello.txt", "world")
            assert "Successfully" in result
            assert (ws / "hello.txt").read_text() == "world"
        finally:
            clear_thread_workspace()

    def test_file_reader_uses_thread_workspace(self, tmp_path):
        ws = tmp_path / "job-read"
        ws.mkdir()
        (ws / "data.txt").write_text("payload")
        set_thread_workspace(str(ws))
        try:
            content = file_reader("data.txt")
            assert content == "payload"
        finally:
            clear_thread_workspace()

    def test_file_lister_uses_thread_workspace(self, tmp_path):
        ws = tmp_path / "job-list"
        ws.mkdir()
        (ws / "a.py").write_text("# a")
        (ws / "b.py").write_text("# b")
        set_thread_workspace(str(ws))
        try:
            listing = file_lister(".")
            assert "a.py" in listing
            assert "b.py" in listing
        finally:
            clear_thread_workspace()

    def test_file_writer_creates_subdirectories(self, tmp_path):
        ws = tmp_path / "job-sub"
        ws.mkdir()
        set_thread_workspace(str(ws))
        try:
            result = file_writer("src/models/user.py", "class User: pass")
            assert "Successfully" in result
            assert (ws / "src" / "models" / "user.py").exists()
        finally:
            clear_thread_workspace()


# ---------------------------------------------------------------------------
# Concurrent file_writer isolation
# ---------------------------------------------------------------------------

class TestConcurrentFileWriterIsolation:
    """Two threads calling file_writer at the same time write to separate workspaces."""

    def test_concurrent_writes_isolated(self, tmp_path):
        ws_a = tmp_path / "job-A"
        ws_b = tmp_path / "job-B"
        ws_a.mkdir()
        ws_b.mkdir()

        barrier = threading.Barrier(2)
        results = {}

        def writer(name, ws, filename, content):
            set_thread_workspace(str(ws))
            barrier.wait()
            results[name] = file_writer(filename, content)
            clear_thread_workspace()

        t_a = threading.Thread(target=writer, args=("A", ws_a, "output.txt", "from-A"))
        t_b = threading.Thread(target=writer, args=("B", ws_b, "output.txt", "from-B"))
        t_a.start(); t_b.start()
        t_a.join(); t_b.join()

        assert (ws_a / "output.txt").read_text() == "from-A"
        assert (ws_b / "output.txt").read_text() == "from-B"
        assert "Successfully" in results["A"]
        assert "Successfully" in results["B"]

    def test_late_arriving_thread_does_not_overwrite_first(self, tmp_path):
        """Thread B starts writing after Thread A, but Thread A's file is untouched."""
        ws_a = tmp_path / "job-A"
        ws_b = tmp_path / "job-B"
        ws_a.mkdir()
        ws_b.mkdir()

        def writer_a():
            set_thread_workspace(str(ws_a))
            file_writer("result.txt", "A-content")
            time.sleep(0.1)   # hold workspace open while B runs
            clear_thread_workspace()

        def writer_b():
            time.sleep(0.03)  # start slightly after A
            set_thread_workspace(str(ws_b))
            file_writer("result.txt", "B-content")
            clear_thread_workspace()

        ta = threading.Thread(target=writer_a)
        tb = threading.Thread(target=writer_b)
        ta.start(); tb.start()
        ta.join(); tb.join()

        assert (ws_a / "result.txt").read_text() == "A-content"
        assert (ws_b / "result.txt").read_text() == "B-content"


# ---------------------------------------------------------------------------
# git_tools thread-local workspace
# ---------------------------------------------------------------------------

class TestGitToolsUseThreadLocal:
    """git_init, git_commit, git_status should resolve workspace via _resolve_workspace."""

    def test_git_init_uses_thread_workspace(self, tmp_path):
        from llamaindex_crew.tools.git_tools import git_init

        ws = tmp_path / "git-job"
        ws.mkdir()
        set_thread_workspace(str(ws))
        try:
            result = git_init()
            assert "initialized" in result.lower() or "already" in result.lower()
            assert (ws / ".git").exists()
        finally:
            clear_thread_workspace()

    def test_git_status_uses_thread_workspace(self, tmp_path):
        import git as gitlib
        from llamaindex_crew.tools.git_tools import git_status

        ws = tmp_path / "git-status-job"
        ws.mkdir()
        repo = gitlib.Repo.init(ws)
        (ws / "init.txt").write_text("init")
        repo.index.add(["init.txt"])
        repo.index.commit("initial")
        set_thread_workspace(str(ws))
        try:
            result = git_status()
            assert "clean" in result.lower() or "untracked" in result.lower() or "modified" in result.lower()
        finally:
            clear_thread_workspace()

    def test_git_commit_uses_thread_workspace(self, tmp_path):
        import git as gitlib
        from llamaindex_crew.tools.git_tools import git_commit

        ws = tmp_path / "git-commit-job"
        ws.mkdir()
        repo = gitlib.Repo.init(ws)
        (ws / "file.txt").write_text("hello")
        repo.index.add(["file.txt"])

        set_thread_workspace(str(ws))
        try:
            result = git_commit("initial commit")
            assert "committed" in result.lower() or "✅" in result
        finally:
            clear_thread_workspace()


# ---------------------------------------------------------------------------
# test_tools thread-local workspace
# ---------------------------------------------------------------------------

class TestTestToolsUseThreadLocal:
    """pytest_runner and code_coverage should resolve workspace via _resolve_workspace."""

    def test_pytest_runner_resolves_thread_workspace(self, tmp_path):
        from llamaindex_crew.tools.test_tools import pytest_runner

        ws = tmp_path / "test-job"
        ws.mkdir()
        set_thread_workspace(str(ws))
        try:
            result = pytest_runner("nonexistent_tests/")
            assert "not found" in result.lower()
        finally:
            clear_thread_workspace()

    def test_code_coverage_resolves_thread_workspace(self, tmp_path):
        from llamaindex_crew.tools.test_tools import code_coverage

        ws = tmp_path / "cov-job"
        ws.mkdir()
        set_thread_workspace(str(ws))
        try:
            result = code_coverage("src/")
            assert isinstance(result, str)
        finally:
            clear_thread_workspace()


# ---------------------------------------------------------------------------
# build_runner sets / clears thread-local workspace
# ---------------------------------------------------------------------------

class TestBuildRunnerThreadLocal:
    """build_runner.run_build_pipeline sets thread-local workspace and cleans up."""

    def test_sets_thread_workspace_to_job_path(self, tmp_path):
        """The workspace seen inside the workflow must be the job workspace."""
        from crew_studio.build_runner import run_build_pipeline

        captured = {}

        class FakeWorkflow:
            def __init__(self, **kw):
                captured["workspace_path"] = kw["workspace_path"]

            def run(self, resume=False):
                captured["resolved_during_run"] = _resolve_workspace()
                return {"status": "completed", "task_validation": {"valid": True}}

        with patch("src.llamaindex_crew.workflows.software_dev_workflow.SoftwareDevWorkflow", FakeWorkflow):
            run_build_pipeline(
                job_id="test-job-1",
                workspace_path=tmp_path,
                vision="test vision",
                config=MagicMock(),
                progress_callback=MagicMock(),
                job_db=MagicMock(),
            )

        assert captured["resolved_during_run"] == tmp_path

    def test_clears_thread_workspace_on_success(self, tmp_path):
        from crew_studio.build_runner import run_build_pipeline

        MockWF = MagicMock(return_value=MagicMock(
            run=MagicMock(return_value={"status": "completed", "task_validation": {"valid": True}})
        ))
        with patch("src.llamaindex_crew.workflows.software_dev_workflow.SoftwareDevWorkflow", MockWF):
            run_build_pipeline(
                job_id="test-cleanup",
                workspace_path=tmp_path,
                vision="v",
                config=MagicMock(),
                progress_callback=MagicMock(),
                job_db=MagicMock(),
            )
        assert getattr(_workspace_local, "workspace_path", None) is None

    def test_clears_thread_workspace_on_failure(self, tmp_path):
        from crew_studio.build_runner import run_build_pipeline

        def boom(**kw):
            wf = MagicMock()
            wf.run.side_effect = RuntimeError("boom")
            return wf

        with patch("src.llamaindex_crew.workflows.software_dev_workflow.SoftwareDevWorkflow", side_effect=boom):
            with pytest.raises(RuntimeError, match="boom"):
                run_build_pipeline(
                    job_id="test-fail",
                    workspace_path=tmp_path,
                    vision="v",
                    config=MagicMock(),
                    progress_callback=MagicMock(),
                    job_db=MagicMock(),
                )
        assert getattr(_workspace_local, "workspace_path", None) is None

    def test_restores_original_env_workspace(self, tmp_path, monkeypatch):
        """After run_build_pipeline, WORKSPACE_PATH env is restored to original value."""
        from crew_studio.build_runner import run_build_pipeline

        monkeypatch.setenv("WORKSPACE_PATH", "/original/workspace")
        MockWF = MagicMock(return_value=MagicMock(
            run=MagicMock(return_value={"status": "completed", "task_validation": {"valid": True}})
        ))
        with patch("src.llamaindex_crew.workflows.software_dev_workflow.SoftwareDevWorkflow", MockWF):
            run_build_pipeline(
                job_id="test-restore",
                workspace_path=tmp_path,
                vision="v",
                config=MagicMock(),
                progress_callback=MagicMock(),
                job_db=MagicMock(),
            )
        assert os.environ["WORKSPACE_PATH"] == "/original/workspace"

    def test_concurrent_build_runners_file_isolation(self, tmp_path):
        """Two concurrent threads using set_thread_workspace + file_writer
        write to their own workspaces and never cross-contaminate.
        This simulates the critical code path inside run_build_pipeline."""
        ws_a = tmp_path / "job-A"
        ws_b = tmp_path / "job-B"
        ws_a.mkdir()
        ws_b.mkdir()

        resolved_in_run = {}
        barrier = threading.Barrier(2)
        errors = []

        def simulate_build(name, ws):
            try:
                set_thread_workspace(str(ws))
                barrier.wait()  # force both threads to race
                resolved_in_run[name] = _resolve_workspace()
                file_writer("marker.txt", f"from-{name}")
                file_writer("src/app.py", f"# app for {name}")
            except Exception as e:
                errors.append(f"{name}: {e}")
            finally:
                clear_thread_workspace()

        ta = threading.Thread(target=simulate_build, args=("A", ws_a))
        tb = threading.Thread(target=simulate_build, args=("B", ws_b))
        ta.start(); tb.start()
        ta.join(); tb.join()

        assert errors == [], f"Errors: {errors}"
        assert resolved_in_run["A"] == ws_a
        assert resolved_in_run["B"] == ws_b
        assert (ws_a / "marker.txt").read_text() == "from-A"
        assert (ws_b / "marker.txt").read_text() == "from-B"
        assert (ws_a / "src" / "app.py").read_text() == "# app for A"
        assert (ws_b / "src" / "app.py").read_text() == "# app for B"


# ---------------------------------------------------------------------------
# Vision coherence (bonus coverage for _check_vision_coherence)
# ---------------------------------------------------------------------------

class TestVisionCoherence:
    """Verify _check_vision_coherence catches mismatches."""

    def test_matching_artifact_passes(self):
        from llamaindex_crew.workflows.software_dev_workflow import _check_vision_coherence

        assert _check_vision_coherence(
            "Build a REST API for employee directory",
            "This design spec defines the employee directory REST API endpoints",
            "design_spec.md",
        ) is True

    def test_mismatched_artifact_fails(self):
        from llamaindex_crew.workflows.software_dev_workflow import _check_vision_coherence

        assert _check_vision_coherence(
            "Build a REST API for employee directory",
            "This calculator app does arithmetic with a display and keyboard",
            "design_spec.md",
        ) is False

    def test_empty_vision_passes(self):
        from llamaindex_crew.workflows.software_dev_workflow import _check_vision_coherence

        assert _check_vision_coherence("", "anything", "test") is True

    def test_keyword_extraction(self):
        from llamaindex_crew.workflows.software_dev_workflow import _extract_vision_keywords

        kws = _extract_vision_keywords("Build a REST API for employee directory service with pagination")
        assert "rest" in kws
        assert "api" in kws
        assert "employee" in kws
        assert "directory" in kws
        assert "pagination" in kws
        # stop words filtered out
        assert "a" not in kws
        assert "for" not in kws
        assert "with" not in kws

    def test_spring_boot_vision_passes_with_java_artifact(self):
        """AC-24 style: vision says Spring Boot + PostgreSQL; artifact mentions Java/Spring → coherent."""
        from llamaindex_crew.workflows.software_dev_workflow import _check_vision_coherence

        vision = "Build a REST API for employee directory. Use Spring Boot with PostgreSQL. Include OpenAPI docs."
        artifact = "This design uses Spring Boot, PostgreSQL, and OpenAPI (springdoc) for the employee directory API."
        assert _check_vision_coherence(vision, artifact, "design_spec.md") is True

    def test_spring_boot_vision_fails_with_python_artifact(self):
        """Vision says Spring Boot + PostgreSQL; artifact describes Python/FastAPI → incoherent."""
        from llamaindex_crew.workflows.software_dev_workflow import _check_vision_coherence

        vision = "Build a REST API for employee directory. Use Spring Boot with PostgreSQL. Include OpenAPI docs."
        artifact = "We will use Python with FastAPI and SQLite. Pydantic models for the employee API."
        assert _check_vision_coherence(vision, artifact, "design_spec.md") is False

    def test_extract_vision_keywords_spring_boot(self):
        """AC-24: keywords from 'Use Spring Boot with PostgreSQL. Include OpenAPI docs.'"""
        from llamaindex_crew.workflows.software_dev_workflow import _extract_vision_keywords

        kws = _extract_vision_keywords("Use Spring Boot with PostgreSQL. Include OpenAPI docs.")
        assert "spring" in kws
        assert "boot" in kws
        assert "postgresql" in kws
        assert "openapi" in kws
        assert "docs" in kws
