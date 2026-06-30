import unittest
from pathlib import Path
from src.llamaindex_crew.orchestrator.task_manager import TaskManager, TaskDefinition


def make_task_manager(db_name: str = "tier_tasks.db") -> TaskManager:
    db_path = Path("tests/unit") / db_name
    if db_path.exists():
        db_path.unlink()
    return TaskManager(db_path, "tier_proj")

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

        # Set up mock skill_prefetch.json with keyword indicators for model, service, controller, and main entrypoint
        import json
        (self.db_path.parent / "skill_prefetch.json").write_text(json.dumps({
            "tech_architect": [
                {
                    "skill_name": "mvc_guidelines",
                    "content": "Uses model entities, services for logic, controllers for api/handlers, and a main application entrypoint."
                }
            ]
        }))

    def tearDown(self):
        if self.db_path.exists():
            self.db_path.unlink()
        prefetch = self.db_path.parent / "skill_prefetch.json"
        if prefetch.exists():
            prefetch.unlink()

    def _task(self, file_path: str) -> TaskDefinition:
        return TaskDefinition(
            task_id=f"file_{file_path.replace('/', '_')}",
            phase="development",
            task_type="file_creation",
            description=f"Create {file_path}",
            metadata={"file_path": file_path},
        )

    def test_inject_structure_scaffolding_from_skill_tree(self):
        """Scaffolding is injected from skill_prefetch.json ASCII tree, not keyword detection."""
        import json
        # Write a skill with an ASCII tree containing files to inject
        (self.db_path.parent / "skill_prefetch.json").write_text(json.dumps({
            "tech_architect": [
                {
                    "skill_name": "java_spring_guidelines",
                    "content": (
                        "Java Spring Boot app structure:\n"
                        "```text\n"
                        "src/\n"
                        "├── main/\n"
                        "│   └── java/\n"
                        "│       └── com/\n"
                        "│           └── example/\n"
                        "│               ├── Application.java\n"
                        "│               ├── model/\n"
                        "│               │   └── User.java\n"
                        "│               └── service/\n"
                        "│                   └── UserService.java\n"
                        "```\n"
                    )
                }
            ]
        }))
        tasks = [
            self._task("src/main/java/com/example/app/controller/Core.java"),
        ]
        original_count = len(tasks)
        new_tasks = self.manager._inject_framework_scaffolding_tasks(tasks, "Java REST API", "Domain")
        # With skill tree present, new files from the tree should be injected
        self.assertGreater(len(new_tasks), original_count)

    def test_inject_structure_scaffolding_no_skill_no_injection(self):
        """Without skill_prefetch.json, no scaffolding is injected (behaviour since skills-first refactor)."""
        tasks = [
            self._task("src/api/handlers.py"),
            self._task("src/utils/helpers.py"),
        ]
        before = len(tasks)
        new_tasks = self.manager._inject_framework_scaffolding_tasks(tasks, "Python API", "Domain")
        # No skill prefetch — task list unchanged
        self.assertEqual(len(new_tasks), before)

    def test_no_scaffolding_if_skills_not_applicable(self):
        import json
        (self.db_path.parent / "skill_prefetch.json").write_text(json.dumps({
            "tech_architect": [
                {
                    "skill_name": "frappe_guidelines",
                    "content": "Frappe uses DocType JSON files and python doctype controller classes."
                }
            ]
        }))
        tasks = [
            self._task("src/api/handlers.py"),
            self._task("src/utils/helpers.py"),
        ]
        new_tasks = self.manager._inject_framework_scaffolding_tasks(tasks, "Frappe API Stack", "Domain")
        paths = [t.metadata["file_path"] for t in new_tasks]
        self.assertNotIn("src/model/core.py", paths)
        self.assertNotIn("src/service/core.py", paths)

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


class TestFileTierClassification(unittest.TestCase):
    """Scaled tier values and test-file pre-check ordering."""

    def tearDown(self):
        db_path = Path("tests/unit/tier_tasks.db")
        if db_path.exists():
            db_path.unlink()

    def test_model_tier_scaled(self):
        assert TaskManager._classify_file_tier("models/invoice.py") == 10

    def test_service_tier_scaled(self):
        assert TaskManager._classify_file_tier("services/invoice_service.py") == 30

    def test_test_file_tier_dedicated(self):
        assert TaskManager._classify_file_tier("tests/test_invoice.py") == 15

    def test_entrypoint_tier_scaled(self):
        assert TaskManager._classify_file_tier("src/main.py") == 80

    def test_test_file_sorts_before_service(self):
        assert TaskManager._classify_file_tier("tests/test_invoice.py") < \
               TaskManager._classify_file_tier("services/invoice_service.py")

    def test_test_file_sorts_after_model(self):
        assert TaskManager._classify_file_tier("tests/test_invoice.py") > \
               TaskManager._classify_file_tier("models/invoice.py")

    def test_repository_tier_scaled(self):
        assert TaskManager._classify_file_tier("repositories/invoice_repo.py") == 20

    def test_mock_file_tier_last(self):
        assert TaskManager._classify_file_tier("mocks/invoice_mock.py") == 90

    def test_default_tier_controller_level(self):
        assert TaskManager._classify_file_tier("lib/helpers.py") == 50

    def test_entrypoint_detection_still_works(self):
        assert TaskManager._has_entrypoint_in_paths(["src/main.py"]) is True
        assert TaskManager._has_entrypoint_in_paths(["tests/test_main.py"]) is False

    def test_scaffolding_path_entrypoint_tier(self):
        tm = make_task_manager()
        assert "main" in tm._scaffolding_path_for_tier("src", ".py", 80)

    def test_scaffolding_path_model_tier(self):
        tm = make_task_manager()
        assert "model" in tm._scaffolding_path_for_tier("src", ".py", 10)


class TestMatchFeatureFiles(unittest.TestCase):
    def setUp(self):
        self.db_path = Path("tests/unit/feature_match_tasks.db")
        if self.db_path.exists():
            self.db_path.unlink()
        self.manager = TaskManager(self.db_path, "feature_match_proj")

    def tearDown(self):
        if self.db_path.exists():
            self.db_path.unlink()

    def test_match_feature_files_returns_empty_when_no_features(self):
        result = self.manager._match_feature_files("services/invoice.py", {})
        self.assertEqual(result, [])

    def test_match_feature_files_matches_by_stem_keyword(self):
        features = {
            "features/invoice.feature": (
                "Feature: Invoice Management\n  Scenario: Create invoice\n"
            ),
        }
        result = self.manager._match_feature_files(
            "services/invoice_service.py", features,
        )
        self.assertIn("features/invoice.feature", result)

    def test_match_feature_files_no_false_positive(self):
        features = {
            "features/payment.feature": "Feature: Payment Processing\n",
        }
        result = self.manager._match_feature_files(
            "services/user_service.py", features,
        )
        self.assertEqual(result, [])


class TestBuildFilePromptFeatureInjection(unittest.TestCase):
    def setUp(self):
        self.db_path = Path("tests/unit/feature_prompt_tasks.db")
        if self.db_path.exists():
            self.db_path.unlink()
        self.workspace = self.db_path.parent
        self.manager = TaskManager(self.db_path, "feature_prompt_proj")
        features_dir = self.workspace / "features"
        features_dir.mkdir(exist_ok=True)
        (features_dir / "invoice.feature").write_text(
            "Feature: Invoice\n  Scenario: Create\n    Given a user\n",
            encoding="utf-8",
        )

    def tearDown(self):
        if self.db_path.exists():
            self.db_path.unlink()
        feat = self.workspace / "features" / "invoice.feature"
        if feat.exists():
            feat.unlink()
        feat_dir = self.workspace / "features"
        if feat_dir.exists():
            feat_dir.rmdir()

    def test_prompt_injects_acceptance_criteria_from_feature_files(self):
        task = TaskDefinition(
            task_id="file_services_invoice_py",
            phase="development",
            task_type="file_creation",
            description="Create invoice service",
            metadata={
                "file_path": "services/invoice_service.py",
                "feature_files": ["features/invoice.feature"],
            },
        )
        prompt = self.manager.build_file_prompt(
            task, user_stories="As a user I want invoices",
        )
        self.assertIn("ACCEPTANCE CRITERIA", prompt)
        self.assertIn("Feature: Invoice", prompt)
        self.assertNotIn("As a user I want invoices", prompt)

    def test_prompt_keeps_user_stories_without_feature_files(self):
        task = TaskDefinition(
            task_id="file_services_invoice_py",
            phase="development",
            task_type="file_creation",
            description="Create invoice service",
            metadata={"file_path": "services/invoice_service.py"},
        )
        prompt = self.manager.build_file_prompt(
            task, user_stories="As a user I want invoices",
        )
        self.assertIn("As a user I want invoices", prompt)
        self.assertNotIn("ACCEPTANCE CRITERIA", prompt)


if __name__ == '__main__':
    unittest.main()
