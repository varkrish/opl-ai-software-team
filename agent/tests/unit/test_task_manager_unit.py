import unittest
from pathlib import Path
from src.llamaindex_crew.orchestrator.task_manager import TaskManager, TaskDefinition

class TestTaskManagerUnit(unittest.TestCase):
    def setUp(self):
        self.db_path = Path("tests/unit/test_tasks.db")
        if self.db_path.exists():
            self.db_path.unlink()
        self.manager = TaskManager(self.db_path, "test_proj")

    def tearDown(self):
        if self.db_path.exists():
            self.db_path.unlink()

    def test_normalize_file_path(self):
        """Test path normalization for task IDs"""
        self.assertEqual(self.manager.normalize_file_path_for_task_id("src/main.py"), "main_py")
        self.assertEqual(self.manager.normalize_file_path_for_task_id("tests/test_api.py"), "test_api_py")
        self.assertEqual(self.manager.normalize_file_path_for_task_id("README.md"), "README_md")
        self.assertEqual(self.manager.normalize_file_path_for_task_id("src/utils/helper.py"), "utils_helper_py")

    def test_update_task_status_by_output(self):
        """Test scanning agent output for completion markers"""
        # Register a task
        task = TaskDefinition(
            task_id="file_calculator_py",
            phase="dev",
            task_type="file_creation",
            description="Create calculator.py"
        )
        self.manager.register_task(task)
        
        # Simulate agent output
        output = "I have finished the task.\n✅ Created src/calculator.py\nFinal Answer: Done."
        self.manager.update_task_status_by_output(output)
        
        status = self.manager.get_task_status("file_calculator_py")
        self.assertEqual(status.value, "completed")

if __name__ == '__main__':
    unittest.main()
