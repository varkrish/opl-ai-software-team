"""Tests for incomplete-file 2nd pass and fail-not-skip behavior."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from llamaindex_crew.orchestrator.task_manager import TaskManager, TaskDefinition, TaskStatus


class TestIncompleteFilePass(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.db_path = self.test_dir / "test_tasks.db"
        self.workspace_path = self.test_dir / "workspace"
        self.workspace_path.mkdir()
        self.manager = TaskManager(self.db_path, "test_proj")

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def _make_workflow(self):
        from llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow

        wf = SoftwareDevWorkflow(
            project_id="test_proj",
            workspace_path=self.workspace_path,
            vision="Build app",
            config=MagicMock(),
        )
        # Rebind task manager to our temp DB
        wf.task_manager = self.manager
        wf.dev_agent = MagicMock()
        wf.agent_backstories = {}
        return wf

    def test_collect_includes_failed_and_skipped(self):
        self.manager.register_task(TaskDefinition(
            task_id="file_a",
            phase="development",
            task_type="file_creation",
            description="Create a.go",
            metadata={"file_path": "a.go"},
        ))
        self.manager.register_task(TaskDefinition(
            task_id="file_b",
            phase="development",
            task_type="file_creation",
            description="Create b.go",
            metadata={"file_path": "b.go"},
        ))
        self.manager.update_task_status(
            "file_a", "failed", "File a.go was not created by the agent",
        )
        self.manager.update_task_status(
            "file_b", "skipped", "File b.go was not created by the agent",
        )

        wf = self._make_workflow()
        ids = wf._collect_incomplete_file_task_ids()
        self.assertEqual(ids, {"file_a", "file_b"})

    def test_incomplete_pass_resets_and_reprocesses(self):
        self.manager.register_task(TaskDefinition(
            task_id="file_go_mod",
            phase="development",
            task_type="file_creation",
            description="Create go.mod",
            metadata={"file_path": "go.mod"},
        ))
        self.manager.update_task_status(
            "file_go_mod", "failed", "File go.mod was not created by the agent",
        )

        wf = self._make_workflow()
        with patch.object(wf, "_process_file_tasks", return_value=1) as mock_proc, \
             patch.dict(os.environ, {"MAX_INCOMPLETE_FILE_PASSES": "1"}):
            count = wf._run_incomplete_file_task_pass()
            self.assertEqual(count, 1)
            mock_proc.assert_called_once()
            called_ids = mock_proc.call_args[0][1]
            self.assertIn("file_go_mod", called_ids)

        self.assertEqual(
            self.manager.get_task_status("file_go_mod"),
            TaskStatus.REGISTERED,
        )

    def test_incomplete_pass_skipped_when_env_zero(self):
        self.manager.register_task(TaskDefinition(
            task_id="file_go_mod",
            phase="development",
            task_type="file_creation",
            description="Create go.mod",
            metadata={"file_path": "go.mod"},
        ))
        self.manager.update_task_status("file_go_mod", "failed", "missing")

        wf = self._make_workflow()
        with patch.object(wf, "_process_file_tasks") as mock_proc, \
             patch.dict(os.environ, {"MAX_INCOMPLETE_FILE_PASSES": "0"}):
            count = wf._run_incomplete_file_task_pass()
            self.assertEqual(count, 0)
            mock_proc.assert_not_called()


if __name__ == "__main__":
    unittest.main()
