import unittest
import tempfile
import shutil
import json
import sqlite3
from pathlib import Path
import sys

# Ensure we can import from src
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from llamaindex_crew.orchestrator.task_manager import TaskManager, TaskDefinition, TaskStatus

class TestTaskManagerRobustness(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.db_path = self.test_dir / "test_tasks.db"
        self.workspace_path = self.test_dir / "workspace"
        self.workspace_path.mkdir()
        self.manager = TaskManager(self.db_path, "test_proj")

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_metadata_based_resolution(self):
        """Test that tasks can be resolved via metadata if exact ID match fails"""
        # Register a task with specific metadata
        task = TaskDefinition(
            task_id="file_pom_xml",
            phase="dev",
            task_type="file_creation",
            description="Create pom.xml",
            metadata={"file_path": "pom.xml"}
        )
        self.manager.register_task(task)
        
        # Simulate agent output mentioning a different path that matches metadata
        output = "✅ Successfully wrote to src/pom.xml"
        # Note: Task ID for src/pom.xml would be file_src_pom_xml, but we want it to match file_pom_xml
        self.manager.update_task_status_by_output(output)
        
        status = self.manager.get_task_status("file_pom_xml")
        self.assertEqual(status, TaskStatus.COMPLETED)

    def test_basename_resolution_fallback(self):
        """Test that tasks can be resolved via basename if full path differs"""
        task = TaskDefinition(
            task_id="file_server_py",
            phase="dev",
            task_type="file_creation",
            description="Create server.py",
            metadata={"file_path": "src/server.py"}
        )
        self.manager.register_task(task)
        
        # Agent output mentions a reorganization
        output = "✅ Created backend/src/server.py"
        self.manager.update_task_status_by_output(output)
        
        status = self.manager.get_task_status("file_server_py")
        self.assertEqual(status, TaskStatus.COMPLETED)

    def test_reconcile_with_filesystem(self):
        """Test physical filesystem check for incomplete tasks"""
        task1 = TaskDefinition(
            task_id="file_readme",
            phase="dev",
            task_type="file_creation",
            description="Create README.md",
            metadata={"file_path": "README.md"}
        )
        task2 = TaskDefinition(
            task_id="file_app_js",
            phase="dev",
            task_type="file_creation",
            description="Create App.js",
            metadata={"file_path": "src/App.js"}
        )
        self.manager.register_task(task1)
        self.manager.register_task(task2)
        
        # Physically create the files
        (self.workspace_path / "README.md").write_text("# Test")
        (self.workspace_path / "src").mkdir(parents=True, exist_ok=True)
        (self.workspace_path / "src" / "App.js").write_text("// Test")
        
        # Reconcile
        self.manager.reconcile_with_filesystem(self.workspace_path)
        
        self.assertEqual(self.manager.get_task_status("file_readme"), TaskStatus.COMPLETED)
        self.assertEqual(self.manager.get_task_status("file_app_js"), TaskStatus.COMPLETED)

    def test_reconcile_with_filesystem_basename_fallback(self):
        """Test physical check with basename fallback (file moved)"""
        task = TaskDefinition(
            task_id="file_main_py",
            phase="dev",
            task_type="file_creation",
            description="Create main.py",
            metadata={"file_path": "src/main.py"}
        )
        self.manager.register_task(task)
        
        # Create it in a different directory
        moved_dir = self.workspace_path / "backend" / "src"
        moved_dir.mkdir(parents=True, exist_ok=True)
        (moved_dir / "main.py").write_text("print('hello')")
        
        # Reconcile
        self.manager.reconcile_with_filesystem(self.workspace_path)
        
        self.assertEqual(self.manager.get_task_status("file_main_py"), TaskStatus.COMPLETED)

    def test_task_definition_with_status_attribute(self):
        """Test that TaskDefinition has status and it's populated from DB"""
        task = TaskDefinition(
            task_id="test_status_task",
            phase="dev",
            task_type="feature",
            description="Testing status field",
            status="registered"
        )
        self.manager.register_task(task)
        
        # Retrieve tasks
        all_tasks = self.manager.get_all_tasks()
        found = next(t for t in all_tasks if t.task_id == "test_status_task")
        
        self.assertEqual(found.status, "registered")
        
        # Update status and check again
        self.manager.update_task_status("test_status_task", "completed")
        all_tasks = self.manager.get_all_tasks()
        found = next(t for t in all_tasks if t.task_id == "test_status_task")
        self.assertEqual(found.status, "completed")

if __name__ == '__main__':
    unittest.main()
