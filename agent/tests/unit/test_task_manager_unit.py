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

class TestBuildFilePrompt(unittest.TestCase):
    """build_file_prompt must NEVER produce 'unknown' as the target filename."""

    def setUp(self):
        self.db_path = Path("tests/unit/test_prompt_tasks.db")
        if self.db_path.exists():
            self.db_path.unlink()
        self.manager = TaskManager(self.db_path, "prompt_proj")

    def tearDown(self):
        if self.db_path.exists():
            self.db_path.unlink()

    # ------------------------------------------------------------------
    # Normal file_creation task — must include the correct file name
    # ------------------------------------------------------------------

    def test_prompt_includes_correct_file_path(self):
        task = TaskDefinition(
            task_id="file_src_main_py",
            phase="development",
            task_type="file_creation",
            description="Create main entry point",
            metadata={"file_path": "src/main.py"},
        )
        prompt = self.manager.build_file_prompt(task)
        self.assertIn("src/main.py", prompt)
        self.assertNotIn("unknown", prompt.lower())

    # ------------------------------------------------------------------
    # BDD feature task (no file_path in metadata) — must NOT say "unknown"
    # ------------------------------------------------------------------

    def test_prompt_never_says_unknown_for_feature_task(self):
        """
        BDD feature tasks registered via register_tasks_from_features() have
        task_type='feature' and no 'file_path' in metadata.
        build_file_prompt must NOT fall back to 'unknown'.
        It must instead produce a behaviour-implementation directive using
        the task description and scenarios.
        """
        task = TaskDefinition(
            task_id="feature_task_management",
            phase="development",
            task_type="feature",
            description="Task Management",
            metadata={
                "scenarios": [
                    "Given a user creates a task",
                    "When they list tasks",
                    "Then the new task appears",
                ]
            },
        )
        prompt = self.manager.build_file_prompt(task)
        self.assertNotIn("`unknown`", prompt)
        self.assertNotIn("Create the file `unknown`", prompt)
        # The feature description must appear in the prompt so the agent knows
        # what to implement.
        self.assertIn("Task Management", prompt)

    def test_prompt_never_says_unknown_when_file_path_is_missing(self):
        """Metadata present but file_path key simply absent → no 'unknown'."""
        task = TaskDefinition(
            task_id="feature_login",
            phase="development",
            task_type="feature",
            description="User Login Feature",
            metadata={},
        )
        prompt = self.manager.build_file_prompt(task)
        self.assertNotIn("`unknown`", prompt)
        self.assertNotIn("Create the file `unknown`", prompt)

    def test_feature_prompt_mentions_scenarios(self):
        """When a feature task has BDD scenarios they should appear in the prompt."""
        task = TaskDefinition(
            task_id="feature_checkout",
            phase="development",
            task_type="feature",
            description="Checkout Flow",
            metadata={
                "scenarios": ["Given a user adds items to cart", "Then total is calculated"]
            },
        )
        prompt = self.manager.build_file_prompt(task)
        self.assertIn("Checkout Flow", prompt)
        # At least one scenario or the word "scenario" should appear
        self.assertTrue(
            "scenario" in prompt.lower() or "Given" in prompt,
            "Scenarios should be surfaced in the feature prompt",
        )

class TestStructureValidation(unittest.TestCase):
    def setUp(self):
        self.db_path = Path("tests/unit/test_structure_tasks.db")
        if self.db_path.exists():
            self.db_path.unlink()
        self.manager = TaskManager(self.db_path, "structure_proj")

    def tearDown(self):
        if self.db_path.exists():
            self.db_path.unlink()

    def test_validate_rejects_folder_only_tree(self):
        tech_stack = """
```text
project/
├── controller/
├── service/
└── pom.xml
```
"""
        result = self.manager.validate_tech_stack_completeness(tech_stack)
        self.assertFalse(result["valid"])
        self.assertTrue(any("concrete" in i.lower() or "source" in i.lower() for i in result["issues"]))

    def test_validate_requires_layers_for_multi_file_project(self):
        tech_stack = """
```text
src/
├── controller/UserController.java
├── config/AppConfig.java
└── util/Helper.java
```
"""
        result = self.manager.validate_tech_stack_completeness(tech_stack)
        self.assertFalse(result["valid"])
        joined = " ".join(result["issues"]).lower()
        self.assertTrue("service" in joined or "model" in joined or "entrypoint" in joined)

    def test_validate_accepts_complete_java_tree(self):
        tech_stack = """
```text
src/main/java/com/example/app/
├── Application.java
├── model/User.java
├── service/UserService.java
└── controller/UserController.java
src/test/java/com/example/app/UserServiceTest.java
```
"""
        result = self.manager.validate_tech_stack_completeness(tech_stack)
        self.assertTrue(result["valid"])


class TestStructureScaffolding(unittest.TestCase):
    def setUp(self):
        self.db_path = Path("tests/unit/test_scaffold_tasks.db")
        if self.db_path.exists():
            self.db_path.unlink()
        self.manager = TaskManager(self.db_path, "scaffold_proj")

    def tearDown(self):
        if self.db_path.exists():
            self.db_path.unlink()

    def _task(self, file_path: str) -> TaskDefinition:
        return TaskDefinition(
            task_id=f"file_{file_path.replace('/', '_')}",
            phase="development",
            task_type="file_creation",
            description=f"Create {file_path}",
            metadata={"file_path": file_path},
        )

    def test_inject_structure_scaffolding_from_controller_only(self):
        tasks = [
            self._task("src/main/java/com/example/app/controller/Core.java"),
            self._task("src/main/java/com/example/app/config/AppConfig.java"),
        ]
        new_tasks = self.manager._inject_framework_scaffolding_tasks(tasks, "Java REST API", "Domain")
        paths = [t.metadata["file_path"] for t in new_tasks]
        self.assertIn("src/main/java/com/example/app/Application.java", paths)
        self.assertIn("src/main/java/com/example/app/model/core.java", paths)
        self.assertIn("src/main/java/com/example/app/service/core.java", paths)

    def test_inject_structure_scaffolding_python_layout(self):
        tasks = [
            self._task("src/api/handlers.py"),
            self._task("src/utils/helpers.py"),
        ]
        new_tasks = self.manager._inject_framework_scaffolding_tasks(tasks, "Python API", "Domain")
        paths = [t.metadata["file_path"] for t in new_tasks]
        self.assertIn("src/main.py", paths)
        self.assertIn("src/model/core.py", paths)
        self.assertIn("src/service/core.py", paths)

    def test_no_scaffolding_when_layers_present(self):
        tasks = [
            self._task("src/main/java/com/example/app/Application.java"),
            self._task("src/main/java/com/example/app/model/User.java"),
            self._task("src/main/java/com/example/app/service/UserService.java"),
            self._task("src/main/java/com/example/app/controller/UserController.java"),
        ]
        before = len(tasks)
        new_tasks = self.manager._inject_framework_scaffolding_tasks(tasks, "Spring", "Domain")
        self.assertEqual(len(new_tasks), before)

if __name__ == '__main__':
    unittest.main()
