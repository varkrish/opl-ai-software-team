"""
TDD: tests for run_tldr, _workspace_has_indexable_source, _extract_search_terms,
prefetch_tldr_context, build_file_prompt tldr integration, and workflow gating.

Written BEFORE implementation — all tests must fail (RED) until the
implementation is complete.

No live subprocess calls in any test — run_tldr is mocked throughout.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# ── Bootstrap path so tests can find the package ─────────────────────────────
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

# Stub heavy optional deps before importing anything from the package
for _mod in [
    "llama_index.llms.ollama",
    "llama_index.embeddings.huggingface",
]:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()


# ══════════════════════════════════════════════════════════════════════════════
# run_tldr
# ══════════════════════════════════════════════════════════════════════════════

class TestRunTldr:
    """Tests for the public run_tldr() wrapper."""

    def test_returns_stdout(self):
        from llamaindex_crew.tools.tldr_tools import run_tldr

        mock_result = MagicMock()
        mock_result.stdout = "some output"
        mock_result.returncode = 0
        mock_result.stderr = ""

        with patch("shutil.which", return_value="/usr/bin/tldr"), \
             patch("subprocess.run", return_value=mock_result):
            result = run_tldr(["structure", "/tmp/ws"])

        assert result == "some output"

    def test_truncates_long_output(self):
        from llamaindex_crew.tools.tldr_tools import run_tldr, _MAX_OUTPUT_CHARS

        long_output = "x" * (_MAX_OUTPUT_CHARS + 500)
        mock_result = MagicMock()
        mock_result.stdout = long_output
        mock_result.returncode = 0
        mock_result.stderr = ""

        with patch("shutil.which", return_value="/usr/bin/tldr"), \
             patch("subprocess.run", return_value=mock_result):
            result = run_tldr(["structure", "/tmp/ws"])

        assert len(result) <= _MAX_OUTPUT_CHARS + 100
        assert "truncated" in result

    def test_returns_error_when_tldr_missing(self):
        from llamaindex_crew.tools.tldr_tools import run_tldr

        with patch("shutil.which", return_value=None):
            result = run_tldr(["structure", "/tmp/ws"])

        assert "not installed" in result.lower() or "not in PATH" in result

    def test_backward_compat_alias(self):
        """_run_tldr must still exist and be the same callable as run_tldr."""
        from llamaindex_crew.tools import tldr_tools

        assert hasattr(tldr_tools, "_run_tldr")
        assert tldr_tools._run_tldr is tldr_tools.run_tldr

    def test_returns_error_string_on_timeout(self):
        import subprocess
        from llamaindex_crew.tools.tldr_tools import run_tldr

        with patch("shutil.which", return_value="/usr/bin/tldr"), \
             patch("subprocess.run", side_effect=subprocess.TimeoutExpired("tldr", 30)):
            result = run_tldr(["structure", "/tmp/ws"])

        assert "timed out" in result.lower()


# ══════════════════════════════════════════════════════════════════════════════
# _workspace_has_indexable_source
# ══════════════════════════════════════════════════════════════════════════════

class TestWorkspaceHasIndexableSource:
    """Tests for _workspace_has_indexable_source()."""

    def test_true_with_python_files(self, tmp_path):
        from llamaindex_crew.tools.tldr_tools import _workspace_has_indexable_source

        src = tmp_path / "src"
        src.mkdir()
        (src / "calculator.py").write_text("def add(a, b): return a + b")
        assert _workspace_has_indexable_source(tmp_path) is True

    def test_false_on_empty_dir(self, tmp_path):
        from llamaindex_crew.tools.tldr_tools import _workspace_has_indexable_source

        assert _workspace_has_indexable_source(tmp_path) is False

    def test_false_when_only_test_files(self, tmp_path):
        from llamaindex_crew.tools.tldr_tools import _workspace_has_indexable_source

        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_calculator.py").write_text("def test_add(): assert 1 + 1 == 2")
        assert _workspace_has_indexable_source(tmp_path) is False

    def test_true_with_typescript_files(self, tmp_path):
        from llamaindex_crew.tools.tldr_tools import _workspace_has_indexable_source

        src = tmp_path / "src"
        src.mkdir()
        (src / "app.ts").write_text("export const main = () => {};")
        assert _workspace_has_indexable_source(tmp_path) is True


# ══════════════════════════════════════════════════════════════════════════════
# _extract_search_terms
# ══════════════════════════════════════════════════════════════════════════════

class TestExtractSearchTerms:
    """Tests for _extract_search_terms()."""

    def _make_task(self, description: str = ""):
        t = MagicMock()
        t.description = description
        return t

    def test_returns_path_stem(self):
        from llamaindex_crew.tools.tldr_tools import _extract_search_terms

        terms = _extract_search_terms("src/calculator/calculator.py", self._make_task(""))
        assert "calculator" in terms

    def test_returns_up_to_three_terms(self):
        from llamaindex_crew.tools.tldr_tools import _extract_search_terms

        terms = _extract_search_terms(
            "src/auth/jwt_service.py",
            self._make_task("implement JWT token validation and expiry handling"),
        )
        assert 1 <= len(terms) <= 3

    def test_skips_stop_words(self):
        from llamaindex_crew.tools.tldr_tools import _extract_search_terms

        terms = _extract_search_terms(
            "src/auth/service.py",
            self._make_task("the implementation for a service"),
        )
        stop_words = {"the", "for", "a", "an", "in", "of", "and", "to", "with", "is", "it"}
        for term in terms:
            assert term.lower() not in stop_words, \
                f"Stop-word '{term}' should be excluded from search terms"

    def test_skips_single_char_words(self):
        from llamaindex_crew.tools.tldr_tools import _extract_search_terms

        terms = _extract_search_terms(
            "src/auth/a.py",
            self._make_task("do x"),
        )
        for term in terms:
            assert len(term) > 1, f"Single-char term '{term}' should be excluded"

    def test_at_least_one_term_always_returned(self):
        """Even with a trivial path, at least one term is returned."""
        from llamaindex_crew.tools.tldr_tools import _extract_search_terms

        terms = _extract_search_terms("main.py", self._make_task(""))
        assert len(terms) >= 1

    def test_handles_nested_path(self):
        from llamaindex_crew.tools.tldr_tools import _extract_search_terms

        terms = _extract_search_terms(
            "apps/invoicing/doctype/invoice/invoice.py",
            self._make_task("create invoice DocType"),
        )
        assert "invoice" in terms


# ══════════════════════════════════════════════════════════════════════════════
# prefetch_tldr_context
# ══════════════════════════════════════════════════════════════════════════════

class TestPrefetchTldrContext:
    """Tests for prefetch_tldr_context() with mocked run_tldr."""

    def _make_config(self, **kwargs):
        cfg = MagicMock()
        cfg.simple_mode_tldr_enabled = kwargs.get("simple_mode_tldr_enabled", True)
        cfg.simple_mode_tldr_max_chars = kwargs.get("simple_mode_tldr_max_chars", 6000)
        cfg.simple_mode_tldr_include_structure = kwargs.get("simple_mode_tldr_include_structure", True)
        cfg.simple_mode_tldr_min_completed_files = kwargs.get("simple_mode_tldr_min_completed_files", 1)
        return cfg

    def _make_task(self, description: str = "implement calculator add function"):
        t = MagicMock()
        t.description = description
        return t

    def test_calls_structure_when_enabled_and_has_source(self, tmp_path):
        from llamaindex_crew.tools.tldr_tools import prefetch_tldr_context

        src = tmp_path / "src"
        src.mkdir()
        (src / "calculator.py").write_text("def add(): pass")

        with patch("shutil.which", return_value="/usr/bin/tldr"), \
             patch("llamaindex_crew.tools.tldr_tools.run_tldr", return_value="structure output") as mock_rt:
            prefetch_tldr_context(
                workspace_path=tmp_path,
                file_path="src/calculator/calculator.py",
                task=self._make_task(),
                completed_files=1,
                lang="python",
                structure_cache={},
                config=self._make_config(),
            )

        calls_args = [c.args[0] for c in mock_rt.call_args_list]
        assert any(args[0] == "structure" for args in calls_args), \
            f"Expected a 'structure' call, got: {calls_args}"

    def test_skips_structure_when_include_structure_false(self, tmp_path):
        from llamaindex_crew.tools.tldr_tools import prefetch_tldr_context

        src = tmp_path / "src"
        src.mkdir()
        (src / "calculator.py").write_text("def add(): pass")

        config = self._make_config(simple_mode_tldr_include_structure=False)
        with patch("shutil.which", return_value="/usr/bin/tldr"), \
             patch("llamaindex_crew.tools.tldr_tools.run_tldr", return_value="output") as mock_rt:
            prefetch_tldr_context(
                workspace_path=tmp_path,
                file_path="src/calculator/calculator.py",
                task=self._make_task(),
                completed_files=1,
                lang="python",
                structure_cache={},
                config=config,
            )

        calls_args = [c.args[0] for c in mock_rt.call_args_list]
        assert not any(args[0] == "structure" for args in calls_args), \
            "Should NOT call 'structure' when simple_mode_tldr_include_structure=False"

    def test_calls_search_per_term(self, tmp_path):
        from llamaindex_crew.tools.tldr_tools import prefetch_tldr_context

        src = tmp_path / "src"
        src.mkdir()
        (src / "calculator.py").write_text("def add(): pass")

        with patch("shutil.which", return_value="/usr/bin/tldr"), \
             patch("llamaindex_crew.tools.tldr_tools.run_tldr", return_value="search output") as mock_rt:
            prefetch_tldr_context(
                workspace_path=tmp_path,
                file_path="src/calculator/calculator.py",
                task=self._make_task(),
                completed_files=1,
                lang="python",
                structure_cache={},
                config=self._make_config(),
            )

        calls_args = [c.args[0] for c in mock_rt.call_args_list]
        assert any(args[0] == "search" for args in calls_args), \
            f"Expected a 'search' call, got: {calls_args}"

    def test_calls_impact_only_when_target_file_exists(self, tmp_path):
        from llamaindex_crew.tools.tldr_tools import prefetch_tldr_context

        # Create the target file on disk (brownfield edit)
        target = tmp_path / "src" / "calculator" / "calculator.py"
        target.parent.mkdir(parents=True)
        target.write_text("def add(): pass")

        with patch("shutil.which", return_value="/usr/bin/tldr"), \
             patch("llamaindex_crew.tools.tldr_tools.run_tldr", return_value="impact output") as mock_rt:
            prefetch_tldr_context(
                workspace_path=tmp_path,
                file_path="src/calculator/calculator.py",
                task=self._make_task(),
                completed_files=1,
                lang="python",
                structure_cache={},
                config=self._make_config(),
            )

        calls_args = [c.args[0] for c in mock_rt.call_args_list]
        assert any(args[0] == "impact" for args in calls_args), \
            "Expected an 'impact' call when target file already exists on disk (brownfield)"

    def test_no_impact_call_when_target_file_absent(self, tmp_path):
        from llamaindex_crew.tools.tldr_tools import prefetch_tldr_context

        # Workspace has source files but target file does NOT exist (greenfield)
        src = tmp_path / "src"
        src.mkdir()
        (src / "other.py").write_text("def foo(): pass")

        with patch("shutil.which", return_value="/usr/bin/tldr"), \
             patch("llamaindex_crew.tools.tldr_tools.run_tldr", return_value="output") as mock_rt:
            prefetch_tldr_context(
                workspace_path=tmp_path,
                file_path="src/calculator/calculator.py",  # doesn't exist in workspace
                task=self._make_task(),
                completed_files=1,
                lang="python",
                structure_cache={},
                config=self._make_config(),
            )

        calls_args = [c.args[0] for c in mock_rt.call_args_list]
        assert not any(args[0] == "impact" for args in calls_args), \
            "Should NOT call 'impact' when target file does not yet exist (greenfield)"

    def test_returns_empty_when_disabled(self, tmp_path):
        from llamaindex_crew.tools.tldr_tools import prefetch_tldr_context

        config = self._make_config(simple_mode_tldr_enabled=False)

        result = prefetch_tldr_context(
            workspace_path=tmp_path,
            file_path="src/calculator.py",
            task=self._make_task(),
            completed_files=5,
            lang="python",
            structure_cache={},
            config=config,
        )
        assert result == ""

    def test_returns_empty_when_tldr_not_in_path(self, tmp_path):
        from llamaindex_crew.tools.tldr_tools import prefetch_tldr_context

        with patch("shutil.which", return_value=None):
            result = prefetch_tldr_context(
                workspace_path=tmp_path,
                file_path="src/calculator.py",
                task=self._make_task(),
                completed_files=5,
                lang="python",
                structure_cache={},
                config=self._make_config(),
            )
        assert result == ""

    def test_returns_empty_when_no_source_and_below_threshold(self, tmp_path):
        from llamaindex_crew.tools.tldr_tools import prefetch_tldr_context

        config = self._make_config(simple_mode_tldr_min_completed_files=2)

        with patch("shutil.which", return_value="/usr/bin/tldr"):
            result = prefetch_tldr_context(
                workspace_path=tmp_path,  # empty workspace
                file_path="src/calculator.py",
                task=self._make_task(),
                completed_files=0,  # below threshold
                lang="python",
                structure_cache={},
                config=config,
            )
        assert result == ""

    def test_output_capped_at_max_chars(self, tmp_path):
        from llamaindex_crew.tools.tldr_tools import prefetch_tldr_context

        src = tmp_path / "src"
        src.mkdir()
        (src / "calculator.py").write_text("def add(): pass")

        max_chars = 200
        config = self._make_config(simple_mode_tldr_max_chars=max_chars)
        big_output = "x" * 10_000

        with patch("shutil.which", return_value="/usr/bin/tldr"), \
             patch("llamaindex_crew.tools.tldr_tools.run_tldr", return_value=big_output):
            result = prefetch_tldr_context(
                workspace_path=tmp_path,
                file_path="src/calculator/calculator.py",
                task=self._make_task(),
                completed_files=1,
                lang="python",
                structure_cache={},
                config=config,
            )

        assert len(result) <= max_chars + 50, \
            f"Output length {len(result)} exceeds cap {max_chars} + 50 margin"

    def test_structure_cache_prevents_second_call(self, tmp_path):
        from llamaindex_crew.tools.tldr_tools import prefetch_tldr_context

        src = tmp_path / "src"
        src.mkdir()
        (src / "calculator.py").write_text("def add(): pass")
        (src / "other.py").write_text("def sub(): pass")

        structure_cache: dict = {}

        with patch("shutil.which", return_value="/usr/bin/tldr"), \
             patch("llamaindex_crew.tools.tldr_tools.run_tldr", return_value="output") as mock_rt:
            # First call — structure cache is empty → should run structure
            prefetch_tldr_context(
                workspace_path=tmp_path,
                file_path="src/calculator.py",
                task=self._make_task(),
                completed_files=1,
                lang="python",
                structure_cache=structure_cache,
                config=self._make_config(),
            )
            first_structure_calls = sum(
                1 for c in mock_rt.call_args_list if c.args[0][0] == "structure"
            )

            mock_rt.reset_mock()

            # Second call — same workspace, structure_cache is now populated
            prefetch_tldr_context(
                workspace_path=tmp_path,
                file_path="src/other.py",
                task=self._make_task(),
                completed_files=2,
                lang="python",
                structure_cache=structure_cache,
                config=self._make_config(),
            )
            second_structure_calls = sum(
                1 for c in mock_rt.call_args_list if c.args[0][0] == "structure"
            )

        assert first_structure_calls >= 1, "First call must run 'structure'"
        assert second_structure_calls == 0, \
            "Second call must NOT re-run 'structure' (should use cache)"

    def test_returns_empty_on_none_config_no_tldr(self, tmp_path):
        """When config is None and tldr is not installed, return empty string."""
        from llamaindex_crew.tools.tldr_tools import prefetch_tldr_context

        with patch("shutil.which", return_value=None):
            result = prefetch_tldr_context(
                workspace_path=tmp_path,
                file_path="src/calculator.py",
                task=self._make_task(),
                completed_files=1,
                lang="python",
                structure_cache={},
                config=None,
            )
        assert result == ""


# ══════════════════════════════════════════════════════════════════════════════
# build_file_prompt — tldr_context integration
# ══════════════════════════════════════════════════════════════════════════════

class TestBuildFilePromptTldrContext:
    """Tests that build_file_prompt includes/excludes the CODEBASE STRUCTURE section."""

    def _make_task_manager(self, tmp_path):
        from llamaindex_crew.orchestrator.task_manager import TaskManager
        db = tmp_path / "tasks.db"
        return TaskManager(db_path=db, project_id="test-proj")

    def _make_task_def(self, file_path: str = "src/calculator.py"):
        from llamaindex_crew.orchestrator.task_manager import TaskDefinition
        return TaskDefinition(
            task_id="t1",
            phase="development",
            task_type="file_creation",
            description="implement calculator",
            metadata={"file_path": file_path},
        )

    def test_includes_tldr_section_when_nonempty(self, tmp_path):
        tm = self._make_task_manager(tmp_path)
        task = self._make_task_def()

        prompt = tm.build_file_prompt(
            task,
            tldr_context="def add(a, b): return a + b\n",
        )

        assert "CODEBASE STRUCTURE" in prompt

    def test_omits_tldr_section_when_empty_string(self, tmp_path):
        tm = self._make_task_manager(tmp_path)
        task = self._make_task_def()

        prompt = tm.build_file_prompt(task, tldr_context="")
        assert "CODEBASE STRUCTURE" not in prompt

    def test_omits_tldr_section_when_none(self, tmp_path):
        tm = self._make_task_manager(tmp_path)
        task = self._make_task_def()

        prompt = tm.build_file_prompt(task, tldr_context=None)
        assert "CODEBASE STRUCTURE" not in prompt

    def test_omits_tldr_section_when_whitespace_only(self, tmp_path):
        tm = self._make_task_manager(tmp_path)
        task = self._make_task_def()

        prompt = tm.build_file_prompt(task, tldr_context="   \n  ")
        assert "CODEBASE STRUCTURE" not in prompt

    def test_tldr_content_appears_in_prompt(self, tmp_path):
        tm = self._make_task_manager(tmp_path)
        task = self._make_task_def()

        unique_snippet = "UNIQUE_TLDR_MARKER_XYZ"
        prompt = tm.build_file_prompt(task, tldr_context=unique_snippet)
        assert unique_snippet in prompt


# ══════════════════════════════════════════════════════════════════════════════
# _process_claimed_task — tldr context gating
# ══════════════════════════════════════════════════════════════════════════════

class TestProcessClaimedTaskTldrGating:
    """Tests that _process_claimed_task calls prefetch_tldr_context appropriately."""

    def _make_config_mock(self, tldr_enabled: bool = True):
        config = MagicMock()
        gen = MagicMock()
        gen.simple_mode_tldr_enabled = tldr_enabled
        gen.simple_mode_skip_rag = True
        gen.simple_mode_max_retries = 0
        gen.simple_mode_retry_critical_only = False
        gen.simple_mode_large_file_related_chars = 16384
        gen.simple_mode_max_tech_stack_chars = 12000
        gen.simple_mode_max_user_stories_chars = 3000
        config.generation = gen
        config.prompt_limits = MagicMock(
            max_project_vision_chars=14000,
            max_completed_file_chars=8192,
        )
        return config, gen

    def _make_workflow(self, tmp_path, config):
        from llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow
        return SoftwareDevWorkflow(
            project_id="test-tldr-gating",
            workspace_path=tmp_path,
            vision="test vision",
            config=config,
        )

    def _make_task_mock(self, file_path: str = "src/calculator.py"):
        task = MagicMock()
        task.task_id = "t-tldr-1"
        task.task_type = "file_creation"
        task.description = "implement calculator"
        task.metadata = {"file_path": file_path, "auto_content": None}
        return task

    def _make_agent(self, supports_react: bool):
        agent = MagicMock()
        agent.supports_react = supports_react
        agent.agent.chat.return_value = "```python\ndef add(): pass\n```"
        agent.agent.reset_chat = MagicMock()
        return agent

    def test_calls_prefetch_when_simple_mode_enabled(self, tmp_path):
        """When agent_simple=True and tldr enabled, prefetch_tldr_context must be called."""
        config, gen = self._make_config_mock(tldr_enabled=True)
        wf = self._make_workflow(tmp_path, config)
        task = self._make_task_mock()
        agent = self._make_agent(supports_react=False)
        lock = threading.Lock()

        # _resolve_task_file_on_disk returns a path that does NOT exist
        # so the task is marked "skipped" and the loop exits cleanly
        non_existent = tmp_path / "src" / "calculator.py"

        with patch("llamaindex_crew.tools.tldr_tools.prefetch_tldr_context",
                   return_value="ctx", create=True) as mock_prefetch, \
             patch.object(wf.task_manager, "get_related_existing_files", return_value={}), \
             patch.object(wf, "_dev_prompt_context", return_value=("", "")), \
             patch.object(wf, "_generation_settings", return_value=gen), \
             patch.object(wf, "_materialize_file_from_response"), \
             patch.object(wf, "_resolve_task_file_on_disk", return_value=non_existent), \
             patch.object(wf.task_manager, "update_task_status"), \
             patch.object(wf.task_manager, "mark_task_executed"), \
             patch("llamaindex_crew.tools.tldr_tools.detect_tldr_lang",
                   return_value="python", create=True):

            wf._process_claimed_task(
                task=task,
                agent=agent,
                label="worker-1",
                completed_files={},
                export_registry={},
                lock=lock,
                task_num=1,
            )

        mock_prefetch.assert_called_once()

    def test_skips_prefetch_when_react_mode(self, tmp_path):
        """When agent_simple=False (ReAct mode), prefetch_tldr_context must NOT be called."""
        config, gen = self._make_config_mock(tldr_enabled=True)
        wf = self._make_workflow(tmp_path, config)
        task = self._make_task_mock()
        agent = self._make_agent(supports_react=True)  # ReAct
        lock = threading.Lock()

        non_existent = tmp_path / "src" / "calculator.py"

        with patch("llamaindex_crew.tools.tldr_tools.prefetch_tldr_context",
                   return_value="ctx", create=True) as mock_prefetch, \
             patch.object(wf.task_manager, "get_related_existing_files", return_value={}), \
             patch.object(wf, "_dev_prompt_context", return_value=("", "")), \
             patch.object(wf, "_generation_settings", return_value=gen), \
             patch.object(wf, "_materialize_file_from_response"), \
             patch.object(wf, "_resolve_task_file_on_disk", return_value=non_existent), \
             patch.object(wf.task_manager, "update_task_status"), \
             patch.object(wf.task_manager, "mark_task_executed"), \
             patch("llamaindex_crew.utils.rag_context.get_phase_rag_context",
                   return_value=""):

            wf._process_claimed_task(
                task=task,
                agent=agent,
                label="worker-1",
                completed_files={},
                export_registry={},
                lock=lock,
                task_num=1,
            )

        mock_prefetch.assert_not_called()

    def test_workflow_has_tldr_structure_cache(self, tmp_path):
        """SoftwareDevWorkflow.__init__ must add _tldr_structure_cache dict."""
        config, _ = self._make_config_mock()
        wf = self._make_workflow(tmp_path, config)
        assert hasattr(wf, "_tldr_structure_cache"), \
            "SoftwareDevWorkflow must have _tldr_structure_cache attribute"
        assert isinstance(wf._tldr_structure_cache, dict)
