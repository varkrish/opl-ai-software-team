"""Unit tests for test plan parsing and feature test runner."""
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.llamaindex_crew.tools.test_tools import (
    _read_test_plan,
    run_feature_tests,
)


class TestReadTestPlan(unittest.TestCase):
    def test_read_test_plan_returns_empty_when_missing(self):
        tmp = Path("tests/unit/_tmp_no_plan")
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            self.assertEqual(_read_test_plan(tmp), {})
        finally:
            tmp.rmdir()

    def test_read_test_plan_parses_both_commands(self):
        tmp = Path("tests/unit/_tmp_plan")
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            (tmp / "test_plan.md").write_text(
                "backend_test_command: pytest tests/\n"
                "frontend_test_command: npm test\n",
                encoding="utf-8",
            )
            result = _read_test_plan(tmp)
            self.assertEqual(result["backend_test_command"], "pytest tests/")
            self.assertEqual(result["frontend_test_command"], "npm test")
        finally:
            (tmp / "test_plan.md").unlink(missing_ok=True)
            tmp.rmdir()


class TestRunFeatureTests(unittest.TestCase):
    def test_skipped_when_syntax_only_default(self):
        tmp = Path("tests/unit/_tmp_syntax_only")
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            with patch.dict("os.environ", {"SMOKE_TEST_BACKEND": "syntax_only"}, clear=False):
                result = run_feature_tests("backend", str(tmp))
            self.assertTrue(result.get("skipped"))
            self.assertTrue(result.get("passed"))
        finally:
            tmp.rmdir()

    def test_skipped_when_no_test_plan(self):
        tmp = Path("tests/unit/_tmp_no_test_plan_run")
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            with patch.dict("os.environ", {"SMOKE_TEST_BACKEND": "podman"}, clear=False):
                result = run_feature_tests("backend", str(tmp))
            self.assertTrue(result.get("skipped"))
        finally:
            tmp.rmdir()

    @patch("src.llamaindex_crew.tools.test_tools._parse_test_output_with_llm")
    @patch("src.llamaindex_crew.tools.test_tools._run_test_command_in_container")
    def test_run_feature_tests_parses_output(self, mock_run, mock_parse):
        tmp = Path("tests/unit/_tmp_run_tests")
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            (tmp / "test_plan.md").write_text(
                "backend_test_command: pytest tests/ -v\n",
                encoding="utf-8",
            )
            mock_run.return_value = (1, "FAILED tests/test_invoice.py")
            mock_parse.return_value = {
                "passed": False,
                "total": 1,
                "passed_count": 0,
                "failed_count": 1,
                "failures": [{"test": "test_invoice", "error": "AssertionError"}],
            }
            with patch.dict("os.environ", {"SMOKE_TEST_BACKEND": "podman"}, clear=False):
                result = run_feature_tests("backend", str(tmp))
            self.assertFalse(result["passed"])
            self.assertEqual(result["failed_count"], 1)
            mock_parse.assert_called_once()
        finally:
            (tmp / "test_plan.md").unlink(missing_ok=True)
            tmp.rmdir()


class TestGenerateTestPlan(unittest.TestCase):
    def test_generate_test_plan_skips_if_already_exists(self):
        import tempfile
        import shutil
        from src.llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow

        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / "test_plan.md").write_text(
                "backend_test_command: pytest\n", encoding="utf-8",
            )
            wf = SoftwareDevWorkflow("test-proj", tmp, "Build an app")
            mock_llm = MagicMock()
            with patch.object(wf, "_get_manager_llm", return_value=mock_llm):
                wf._generate_test_plan()
            mock_llm.complete.assert_not_called()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_generate_test_plan_is_nonfatal_on_llm_error(self):
        import tempfile
        import shutil
        from src.llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow

        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / "tech_stack.md").write_text("# stack\n", encoding="utf-8")
            wf = SoftwareDevWorkflow("test-proj", tmp, "Build an app")
            mock_llm = MagicMock()
            mock_llm.complete.side_effect = RuntimeError("LLM unavailable")
            with patch.object(wf, "_get_manager_llm", return_value=mock_llm):
                with patch(
                    "src.llamaindex_crew.tools.skill_tools.prefetch_skills",
                    return_value="",
                ):
                    wf._generate_test_plan()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
