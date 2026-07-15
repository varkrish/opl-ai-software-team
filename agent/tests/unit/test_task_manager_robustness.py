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
        (self.workspace_path / "src" / "App.js").write_text("import React from 'react';\n\nfunction App() {\n  return (\n    <div>\n      <h1>Real app logic</h1>\n    </div>\n  );\n}\nexport default App;\n")
        
        # Reconcile
        self.manager.reconcile_with_filesystem(self.workspace_path)
        
        self.assertEqual(self.manager.get_task_status("file_readme"), TaskStatus.COMPLETED)
        self.assertEqual(self.manager.get_task_status("file_app_js"), TaskStatus.COMPLETED)

    def test_reconcile_with_filesystem_basename_fallback(self):
        """Test physical check with suffix fallback (file moved to deeper path)"""
        task = TaskDefinition(
            task_id="file_main_py",
            phase="dev",
            task_type="file_creation",
            description="Create main.py",
            metadata={"file_path": "src/main.py"}
        )
        self.manager.register_task(task)

        # Create it in a different directory (suffix match, not ambiguous basename)
        moved_dir = self.workspace_path / "backend" / "src"
        moved_dir.mkdir(parents=True, exist_ok=True)
        (moved_dir / "main.py").write_text("import sys\n\ndef main():\n    print('hello world')\n    print('this is a real script')\n    return 0\n\nif __name__ == '__main__':\n    sys.exit(main())\n")

        self.manager.reconcile_with_filesystem(self.workspace_path)

        self.assertEqual(self.manager.get_task_status("file_main_py"), TaskStatus.COMPLETED)

    def test_resolve_planned_file_rejects_ambiguous_handler_basename(self):
        """handler.go in different packages must not cross-match."""
        create_dir = self.workspace_path / "internal" / "create"
        delete_dir = self.workspace_path / "internal" / "delete"
        create_dir.mkdir(parents=True, exist_ok=True)
        delete_dir.mkdir(parents=True, exist_ok=True)
        (create_dir / "handler.go").write_text("package create")
        # delete/handler.go intentionally missing

        resolved = TaskManager.resolve_planned_file_on_disk(
            self.workspace_path, "internal/delete/handler.go"
        )
        self.assertIsNone(resolved)

    def test_reconcile_does_not_complete_delete_handler_via_create_handler(self):
        """Regression: delete handler task must not match create/handler.go by basename."""
        self.manager.register_task(TaskDefinition(
            task_id="file_internal_delete_handler_go",
            phase="dev",
            task_type="file_creation",
            description="Create delete handler",
            metadata={"file_path": "internal/delete/handler.go"},
        ))
        create_dir = self.workspace_path / "internal" / "create"
        create_dir.mkdir(parents=True, exist_ok=True)
        (create_dir / "handler.go").write_text("package create")

        self.manager.reconcile_with_filesystem(self.workspace_path)

        self.assertEqual(
            self.manager.get_task_status("file_internal_delete_handler_go"),
            TaskStatus.REGISTERED,
        )

    def test_resolve_planned_file_rejects_config_path_collision(self):
        """janitor config must not match root config/config.go by basename."""
        config_dir = self.workspace_path / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "config.go").write_text("package config")

        resolved = TaskManager.resolve_planned_file_on_disk(
            self.workspace_path, "internal/janitor/config.go"
        )
        self.assertIsNone(resolved)

    def test_finalize_marks_missing_files_failed_not_skipped(self):
        """Unimplemented file tasks should be failed for accurate validation."""
        self.manager.register_task(TaskDefinition(
            task_id="file_internal_execute_handler_go",
            phase="dev",
            task_type="file_creation",
            description="Create execute handler",
            metadata={"file_path": "internal/execute/handler.go"},
        ))

        self.manager.finalize_incomplete_tasks(self.workspace_path)

        self.assertEqual(
            self.manager.get_task_status("file_internal_execute_handler_go"),
            TaskStatus.FAILED,
        )

    def test_finalize_preserves_failed_feature_tasks(self):
        """Feature tasks that already failed (e.g. LLM error) must not be auto-completed."""
        self.manager.register_task(TaskDefinition(
            task_id="feature_execute_sandbox",
            phase="dev",
            task_type="feature",
            description="Execute sandbox",
            status="failed",
        ))
        self.manager.mark_task_executed(
            "feature_execute_sandbox",
            TaskStatus.FAILED,
            "HTTP 400: LLM error",
        )

        self.manager.finalize_incomplete_tasks(self.workspace_path)

        self.assertEqual(
            self.manager.get_task_status("feature_execute_sandbox"),
            TaskStatus.FAILED,
        )
        validation = self.manager.validate_all_tasks_completed(self.workspace_path)
        self.assertFalse(validation["valid"])
        self.assertIn("feature_execute_sandbox", validation["failed_tasks"])

    def test_output_matching_suffix_without_basename_collision(self):
        """Output path suffix match still works for reorganized projects."""
        self.manager.register_task(TaskDefinition(
            task_id="file_server_py",
            phase="dev",
            task_type="file_creation",
            description="Create server.py",
            metadata={"file_path": "src/server.py"},
        ))
        self.manager.update_task_status_by_output("✅ Created backend/src/server.py")
        self.assertEqual(
            self.manager.get_task_status("file_server_py"),
            TaskStatus.COMPLETED,
        )

    def test_output_matching_rejects_handler_basename_collision(self):
        """Creating create/handler.go must not complete delete/handler.go task."""
        self.manager.register_task(TaskDefinition(
            task_id="file_delete_handler",
            phase="dev",
            task_type="file_creation",
            description="Create delete handler",
            metadata={"file_path": "internal/delete/handler.go"},
        ))
        self.manager.update_task_status_by_output("✅ Created internal/create/handler.go")
        self.assertEqual(
            self.manager.get_task_status("file_delete_handler"),
            TaskStatus.REGISTERED,
        )

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

    def test_blacklist_accepts_go_mod_go_sum_dockerfile(self):
        """Registration gate accepts go.mod/go.sum/Dockerfile and rejects images/binaries."""
        from llamaindex_crew.orchestrator.task_manager import _is_valid_file_path

        for path in (
            "go.mod",
            "go.sum",
            "internal/store/store.go",
            "Cargo.toml",
            "Cargo.lock",
            "Dockerfile",
            "Containerfile",
            "Makefile",
            "app.svg",
            "package.json",
        ):
            self.assertTrue(_is_valid_file_path(path), f"expected accept: {path}")

        for path in (
            "1.",
            "2.",
            "photo.png",
            "logo.jpg",
            "lib.so",
            "evil.exe",
            "native.dylib",
            "mod.class",
            "",
            "x",
        ):
            self.assertFalse(_is_valid_file_path(path), f"expected reject: {path}")

    def test_register_granular_includes_go_mod(self):
        """tech_stack tree with go.mod/go.sum must produce file_creation tasks."""
        tech_stack = """
## File Structure
```
myapp/
├── go.mod
├── go.sum
├── main.go
├── Dockerfile
└── internal/
    └── store/
        └── store.go
```
"""
        tasks = self.manager.register_granular_tasks("", tech_stack)
        paths = {(t.metadata or {}).get("file_path") for t in tasks}
        self.assertIn("go.mod", paths)
        self.assertIn("go.sum", paths)
        self.assertIn("Dockerfile", paths)
        self.assertIn("main.go", paths)
        self.assertIn("internal/store/store.go", paths)

    def test_reset_tasks_for_retry(self):
        """reset_tasks_for_retry returns failed/skipped tasks to registered."""
        self.manager.register_task(TaskDefinition(
            task_id="file_go_mod",
            phase="dev",
            task_type="file_creation",
            description="Create go.mod",
            metadata={"file_path": "go.mod"},
        ))
        self.manager.update_task_status(
            "file_go_mod", "failed", "File go.mod was not created by the agent",
        )
        n = self.manager.reset_tasks_for_retry(["file_go_mod"])
        self.assertEqual(n, 1)
        self.assertEqual(
            self.manager.get_task_status("file_go_mod"),
            TaskStatus.REGISTERED,
        )

    def test_reconcile_corrupt_completed_files_marks_failed(self):
        """Completed file tasks with truncated on-disk content are marked failed."""
        corrupt = self.workspace_path / "internal" / "http" / "handlers.go"
        corrupt.parent.mkdir(parents=True)
        corrupt.write_text("package http\n\nimport (\n    \\", encoding="utf-8")

        self.manager.register_task(TaskDefinition(
            task_id="file_handlers",
            phase="development",
            task_type="file_creation",
            description="Create handlers.go",
            metadata={"file_path": "internal/http/handlers.go"},
        ))
        self.manager.update_task_status(
            "file_handlers", "completed", "File created: internal/http/handlers.go",
        )

        n = self.manager.reconcile_corrupt_completed_files(self.workspace_path)
        self.assertEqual(n, 1)
        self.assertEqual(
            self.manager.get_task_status("file_handlers"),
            TaskStatus.FAILED,
        )

    def test_reconcile_planning_monologue_completed_file_marks_failed(self):
        """ReAct planning prose written as a .go file is reconciled to failed."""
        monologue = (
            "We cannot run code search tool yet. Use code_search.\n\n"
            "Let's search for metrics usage.\n\n"
            "We'll call file_reader with path.\n"
        )
        corrupt = self.workspace_path / "internal" / "handler" / "metrics_handler.go"
        corrupt.parent.mkdir(parents=True)
        corrupt.write_text(monologue, encoding="utf-8")

        self.manager.register_task(TaskDefinition(
            task_id="file_metrics_handler",
            phase="development",
            task_type="file_creation",
            description="Create metrics_handler.go",
            metadata={"file_path": "internal/handler/metrics_handler.go"},
        ))
        self.manager.update_task_status(
            "file_metrics_handler", "completed", "File created: internal/handler/metrics_handler.go",
        )

        n = self.manager.reconcile_corrupt_completed_files(self.workspace_path)
        self.assertEqual(n, 1)
        self.assertEqual(
            self.manager.get_task_status("file_metrics_handler"),
            TaskStatus.FAILED,
        )

    def test_todo_in_complete_go_file_not_flagged(self):
        """TODO comments in otherwise complete files should not block validation."""
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        p = self.workspace_path / "handler.go"
        p.write_text(
            "package http\n\n"
            "import \"net/http\"\n\n"
            "// TODO: add rate limiting\n"
            "func Create(w http.ResponseWriter, r *http.Request) {\n"
            "    w.WriteHeader(http.StatusCreated)\n"
            "}\n",
            encoding="utf-8",
        )
        result = CodeCompletenessValidator.validate_file(p)
        self.assertTrue(result["complete"], result.get("issues"))


if __name__ == '__main__':
    unittest.main()
