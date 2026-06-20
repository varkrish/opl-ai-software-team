"""
Unit tests for CodeSafetyChecker and the file_writer safety integration.

Two classes of bugs found in production:

1. False-positive word-boundary match — `eval\s*\(` matched the word "Retrieval"
   in markdown documentation because there was no \b word boundary in the regex.
   e.g. "Task Retrieval (All)" → "eval (" → BLOCKED

2. Non-code files (`.md`, `.yaml`, `.json`, `.txt`, `.feature`, etc.) were run
   through Python code safety checks because the language-detection in
   file_tools.py had no else-clause, falling back to `language='python'` for
   any unknown extension.
"""
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "agent" / "src"))

from llamaindex_crew.utils.code_safety import CodeSafetyChecker


# ---------------------------------------------------------------------------
# 1. Word-boundary: "Retrieval" must NOT match the eval pattern
# ---------------------------------------------------------------------------

class TestEvalWordBoundary:
    """eval\s*\( must only match standalone `eval(`, not words containing eval."""

    def setup_method(self):
        self.checker = CodeSafetyChecker()

    def test_retrieval_does_not_trigger_eval_python(self):
        """'Task Retrieval (All)' in a markdown doc must not match the eval pattern."""
        content = "- **Task Retrieval (All)**:\n  - Request: Empty JSON body."
        result = self.checker.check_code(content, "python")
        eval_issues = [i for i in result["issues"] if "eval" in i.lower()]
        assert eval_issues == [], (
            f"False-positive eval match in markdown content: {eval_issues}"
        )

    def test_retrieval_in_design_spec_does_not_block(self):
        """Design spec markdown with REST API docs must not be blocked."""
        content = (
            "## API Endpoints\n"
            "- GET /tasks: Retrieve all tasks\n"
            "- GET /tasks/{task_id}: Retrieve a specific task\n"
            "- **Task Retrieval (All)**:\n"
            "  - Request: Empty JSON body.\n"
            "  - Response: JSON array of tasks.\n"
            "- **Task Retrieval (Single)**:\n"
            "  - Request: Task ID in URL path.\n"
        )
        result = self.checker.check_code(content, "python")
        assert not result["blocked"], (
            f"Markdown REST docs were incorrectly blocked: {result['issues']}"
        )

    def test_real_eval_call_is_still_blocked_python(self):
        """Actual eval( usage in Python code must still be blocked."""
        content = "result = eval(user_input)"
        result = self.checker.check_code(content, "python")
        assert result["blocked"], "Real eval() call must be blocked"
        assert any("eval" in i.lower() for i in result["issues"])

    def test_real_eval_call_is_still_blocked_javascript(self):
        """Actual eval( usage in JS must still be blocked."""
        content = "const x = eval(userCode);"
        result = self.checker.check_code(content, "javascript")
        assert result["blocked"], "Real eval() call in JS must be blocked"

    def test_word_evaluate_does_not_match(self):
        """Words like 'evaluate', 'evaluation' must not trigger the pattern."""
        content = "# Code Evaluation\nWe evaluate performance using benchmarks."
        result = self.checker.check_code(content, "python")
        eval_issues = [i for i in result["issues"] if "Code evaluation" in i]
        assert eval_issues == [], (
            f"'evaluate'/'evaluation' triggered false positive: {eval_issues}"
        )

    def test_eval_at_start_of_line_matched(self):
        """eval( at line start (no preceding word chars) must match."""
        content = "eval(dangerous_input)"
        result = self.checker.check_code(content, "python")
        assert result["blocked"]

    def test_eval_after_space_matched(self):
        """Space before eval( must still match."""
        content = "x = eval(user_input)"
        result = self.checker.check_code(content, "python")
        assert result["blocked"]


# ---------------------------------------------------------------------------
# 2. Non-code files must skip Python / JS code safety patterns
# ---------------------------------------------------------------------------

class TestNonCodeFileLanguage:
    """
    Markdown, YAML, JSON, .txt, .feature files must not be subjected to
    Python/JS code evaluation pattern matching.
    """

    def setup_method(self):
        self.checker = CodeSafetyChecker()

    @pytest.mark.parametrize("language", ["markdown", "text", "yaml", "json", "none"])
    def test_eval_word_in_non_code_not_blocked(self, language):
        """'Retrieval (All)' in any non-code language must not be blocked."""
        content = "Task Retrieval (All): returns every task."
        result = self.checker.check_code(content, language)
        assert not result["blocked"], (
            f"Non-code language '{language}' should not run Python patterns: "
            f"{result['issues']}"
        )

    def test_check_code_unknown_language_has_no_patterns(self):
        """An unknown/unsupported language should produce zero issues."""
        content = "eval(bad) exec(bad) os.system(bad)"
        result = self.checker.check_code(content, "cobol")
        # Unknown language — no patterns defined — must not block
        assert not result["blocked"]
        assert result["issues"] == []


# ---------------------------------------------------------------------------
# 3. check_file_write: .md extension must use a safe (non-Python) language
# ---------------------------------------------------------------------------

class TestCheckFileWriteExtensionMapping:
    """check_file_write must choose the right language based on file extension."""

    def setup_method(self):
        self.checker = CodeSafetyChecker()

    def test_md_file_with_retrieval_not_blocked(self):
        """Writing design_spec.md must never be blocked by eval patterns."""
        content = (
            "## Design\n"
            "- Task Retrieval (All): GET /tasks\n"
            "- Task Retrieval (Single): GET /tasks/{id}\n"
        )
        result = self.checker.check_file_write("design_spec.md", content)
        assert not result["blocked"], (
            f"design_spec.md blocked by false-positive: {result['issues']}"
        )

    def test_py_file_with_eval_is_blocked(self):
        """Writing a .py file containing eval() must be blocked."""
        result = self.checker.check_file_write("main.py", "x = eval(input())")
        assert result["blocked"]

    def test_yaml_file_not_blocked_by_python_patterns(self):
        """Writing a .yaml config with non-code content must not be blocked."""
        content = "service:\n  endpoints:\n    retrieve_all: /tasks\n"
        result = self.checker.check_file_write("config.yaml", content)
        assert not result["blocked"]

    def test_json_file_not_blocked(self):
        content = '{"endpoint": "/tasks", "method": "GET"}'
        result = self.checker.check_file_write("schema.json", content)
        assert not result["blocked"]

    def test_feature_file_not_blocked(self):
        content = (
            "Feature: Task Retrieval\n"
            "  Scenario: Retrieve all tasks\n"
            "    Given tasks exist\n"
            "    When I call GET /tasks\n"
            "    Then I receive a list\n"
        )
        result = self.checker.check_file_write("tasks.feature", content)
        assert not result["blocked"]
