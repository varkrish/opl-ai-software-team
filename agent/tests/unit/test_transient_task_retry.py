"""Unit tests for task-level transient retry in _process_claimed_task.

When agent.chat() raises a 503-like error, the task should be retried
up to MAX_TRANSIENT_RETRIES times (with sleep) before being marked FAILED.
Quality-retry attempts (code issues) are NOT consumed by transient retries.
"""
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest


def _make_workflow(tmp_path):
    """Build a minimal SoftwareDevWorkflow stub with only the fields
    _process_claimed_task needs."""
    sys_path_patch = pytest.importorskip(
        "llamaindex_crew.workflows.software_dev_workflow",
        reason="workflow module not importable in this environment",
    )
    from llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow

    wf = object.__new__(SoftwareDevWorkflow)
    wf.workspace_path = tmp_path
    wf.vision = "test"
    wf.tech_stack = ""
    wf.user_stories = ""
    wf.api_contract = None
    wf.config = None
    wf._tldr_structure_cache = {}
    wf._export_registry = {}
    wf.document_indexer = MagicMock()
    wf.document_indexer.query.return_value = ""
    wf.budget_tracker = None
    wf.progress_callback = None

    from llamaindex_crew.orchestrator.task_manager import TaskManager, TaskDefinition
    db_path = tmp_path / "tasks.db"
    wf.task_manager = TaskManager(db_path, "proj-test")

    return wf


def _make_task(tmp_path, task_manager):
    from llamaindex_crew.orchestrator.task_manager import TaskDefinition, TaskStatus
    task = TaskDefinition(
        task_id="file_main_py",
        phase="development",
        task_type="file_creation",
        description="Create file: main.py",
        source="tech_stack",
        metadata={"file_path": "main.py"},
    )
    task_manager.register_task(task)
    task_manager.mark_task_started("file_main_py")
    return task


class TestTransientTaskRetry:
    def test_retries_on_503_then_succeeds(self, tmp_path):
        """After two 503s the task should succeed on the third call."""
        try:
            wf = _make_workflow(tmp_path)
        except Exception:
            pytest.skip("Workflow not importable in this env")

        task = _make_task(tmp_path, wf.task_manager)

        agent = MagicMock()
        agent.supports_react = True

        call_count = [0]
        err_503 = Exception("HTTP 503: Service Unavailable")

        def chat_side_effect(prompt):
            call_count[0] += 1
            if call_count[0] < 3:
                raise err_503
            # Third call succeeds; also write the file so validation passes
            (tmp_path / "main.py").write_text("print('hello')")
            return MagicMock()

        agent.agent.chat = chat_side_effect
        agent.agent.reset_chat = MagicMock()

        lock = threading.Lock()
        completed_files = {}
        export_registry = {}

        with patch("time.sleep"):
            with patch.object(
                wf, "_generation_settings", return_value=None
            ), patch.object(
                wf, "_dev_prompt_context", return_value=("", "")
            ), patch.object(
                wf, "_resolve_task_file_on_disk", return_value=tmp_path / "main.py"
            ), patch.object(
                wf, "_materialize_file_from_response"
            ), patch(
                "llamaindex_crew.workflows.software_dev_workflow.get_phase_rag_context",
                return_value="",
            ), patch(
                "llamaindex_crew.orchestrator.code_validator.CodeCompletenessValidator"
            ) as mock_cv:
                mock_cv.validate_file_integration.return_value = {"issues": []}
                mock_cv.validate_file.return_value = {"issues": []}
                mock_cv.extract_export_summary.return_value = {"exports": []}

                wf._process_claimed_task(
                    task, agent, "test", completed_files, export_registry, lock, 1
                )

        from llamaindex_crew.orchestrator.task_manager import TaskStatus
        status = wf.task_manager.get_task_status("file_main_py")
        assert status == TaskStatus.COMPLETED, f"Expected COMPLETED, got {status}"
        assert call_count[0] == 3, f"Expected 3 chat calls, got {call_count[0]}"

    def test_fails_after_max_transient_retries(self, tmp_path):
        """Task should be marked FAILED after MAX_TRANSIENT_RETRIES exhausted."""
        try:
            wf = _make_workflow(tmp_path)
        except Exception:
            pytest.skip("Workflow not importable in this env")

        task = _make_task(tmp_path, wf.task_manager)

        agent = MagicMock()
        agent.supports_react = True

        call_count = [0]

        def chat_always_503(prompt):
            call_count[0] += 1
            raise Exception("HTTP 503: Service Unavailable")

        agent.agent.chat = chat_always_503
        agent.agent.reset_chat = MagicMock()

        lock = threading.Lock()

        with patch("time.sleep"):
            with patch.object(
                wf, "_generation_settings", return_value=None
            ), patch.object(
                wf, "_dev_prompt_context", return_value=("", "")
            ), patch.object(
                wf, "_resolve_task_file_on_disk", return_value=tmp_path / "main.py"
            ), patch.object(
                wf, "_materialize_file_from_response"
            ), patch(
                "llamaindex_crew.workflows.software_dev_workflow.get_phase_rag_context",
                return_value="",
            ):
                wf._process_claimed_task(
                    task, agent, "test", {}, {}, lock, 1
                )

        from llamaindex_crew.orchestrator.task_manager import TaskStatus
        status = wf.task_manager.get_task_status("file_main_py")
        assert status == TaskStatus.FAILED, f"Expected FAILED, got {status}"
        # 1 original + 3 transient retries = 4 total calls
        assert call_count[0] == 4, f"Expected 4 chat calls, got {call_count[0]}"

    def test_non_transient_error_fails_immediately(self, tmp_path):
        """Non-503 errors must not trigger transient retries."""
        try:
            wf = _make_workflow(tmp_path)
        except Exception:
            pytest.skip("Workflow not importable in this env")

        task = _make_task(tmp_path, wf.task_manager)

        agent = MagicMock()
        agent.supports_react = True

        call_count = [0]

        def chat_auth_error(prompt):
            call_count[0] += 1
            raise Exception("401 Unauthorized: invalid API key")

        agent.agent.chat = chat_auth_error
        agent.agent.reset_chat = MagicMock()

        lock = threading.Lock()

        with patch("time.sleep") as mock_sleep:
            with patch.object(
                wf, "_generation_settings", return_value=None
            ), patch.object(
                wf, "_dev_prompt_context", return_value=("", "")
            ), patch.object(
                wf, "_resolve_task_file_on_disk", return_value=tmp_path / "main.py"
            ), patch.object(
                wf, "_materialize_file_from_response"
            ), patch(
                "llamaindex_crew.workflows.software_dev_workflow.get_phase_rag_context",
                return_value="",
            ):
                wf._process_claimed_task(
                    task, agent, "test", {}, {}, lock, 1
                )

        from llamaindex_crew.orchestrator.task_manager import TaskStatus
        status = wf.task_manager.get_task_status("file_main_py")
        assert status == TaskStatus.FAILED
        assert call_count[0] == 1, "Should fail immediately without retries"
        mock_sleep.assert_not_called()
