"""Test-runner output is parsed by code, never by a model.

The old ``_parse_test_output_with_llm`` spent a worker-LLM call turning pytest
output into JSON, then regexed the JSON back out of the response.  It crashed a
job: a reasoning model hit ``finish_reason: length`` and returned a
CompletionResponse with null content, which ``str(response)`` could not survive.

That call was already nearly redundant.  The exit code overrode the model's
verdict whenever the two disagreed, and raw_output was retained regardless — so
the only thing the model contributed that code did not already have was the
counts, which render as cosmetic "Backend: 3/5 passed" text.

The replacement contract is universal across languages:

    exit_code   -> pass/fail       authoritative, every runner
    raw_output  -> critique text   already captured
    counts      -> best-effort, absent when unparseable

The dangerous direction here is a WRONG VERDICT.  A missing count renders as
"counts unavailable" and costs nothing; a wrong ``passed`` either ships a broken
build or burns the whole iteration budget chasing a phantom failure.  So every
test below that pits the exit code against the text asserts the exit code wins,
and the parser must never raise — garbage in yields no numbers, not a traceback.

Counts are absent rather than zero when unknown.  ``0/? passed`` reads as a
real, measured zero; the distinction between "nothing passed" and "we could not
tell" is exactly what the small model downstream needs.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.llamaindex_crew.tools.test_tools import _parse_test_output


# ── real runner output fixtures ──────────────────────────────────────────────

PYTEST_MIXED = (
    "============================= test session starts ==============================\n"
    "collected 5 items\n\n"
    "tests/test_task.py ..F.F                                                 [100%]\n\n"
    "=========================== short test summary info ============================\n"
    "FAILED tests/test_task.py::test_create - AssertionError\n"
    "========================= 2 failed, 3 passed in 0.12s ==========================\n"
)

PYTEST_ALL_GREEN = (
    "============================= test session starts ==============================\n"
    "collected 3 items\n\n"
    "tests/test_task.py ...                                                   [100%]\n\n"
    "============================== 3 passed in 0.02s ===============================\n"
)

JEST_MIXED = (
    "Test Suites: 1 failed, 2 passed, 3 total\n"
    "Tests:       1 failed, 7 passed, 8 total\n"
    "Time:        1.204 s\n"
)

VITEST_MIXED = (
    " Test Files  1 failed | 1 passed (2)\n"
    "      Tests  1 failed | 3 passed (4)\n"
)

CARGO_MIXED = (
    "running 4 tests\n"
    "test tests::create ... ok\n"
    "test tests::delete ... FAILED\n\n"
    "test result: ok. 3 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out\n"
)

SUREFIRE_MIXED = (
    "[INFO] -------------------------------------------------------\n"
    "[INFO]  T E S T S\n"
    "[INFO] -------------------------------------------------------\n"
    "[INFO] Running com.example.task.TaskServiceTest\n"
    "[ERROR] Tests run: 6, Failures: 1, Errors: 1, Skipped: 0\n"
)

TAP_MIXED = (
    "TAP version 13\n"
    "1..4\n"
    "ok 1 - creates a task\n"
    "not ok 2 - rejects empty name\n"
    "ok 3 - lists tasks\n"
    "ok 4 - deletes a task\n"
)

GO_MIXED = (
    "ok   github.com/example/task/service  0.014s\n"
    "FAIL github.com/example/task/handler  0.008s\n"
    "ok   github.com/example/task/store    0.003s\n"
    "FAIL\n"
)

# A runner nobody wrote a pattern for.  Zig today, something else tomorrow.
UNKNOWN_RUNNER = (
    "zig build test\n"
    "All 12 checks completed successfully.\n"
)


class TestExitCodeIsAuthoritative(unittest.TestCase):
    """The exit code decides pass/fail. Nothing in the text may override it."""

    def test_zero_exit_is_pass(self):
        self.assertTrue(_parse_test_output(0, PYTEST_ALL_GREEN)["passed"])

    def test_nonzero_exit_is_fail(self):
        self.assertFalse(_parse_test_output(1, PYTEST_MIXED)["passed"])

    def test_nonzero_exit_beats_green_looking_text(self):
        """
        The regression this whole change exists to prevent.  A runner can print
        a cheerful summary and still exit non-zero — a post-test lint step, a
        coverage gate, a crashed teardown.  The old LLM read the text and said
        "passed"; only a hardcoded exit-code override at the call site saved it.
        Now the exit code is the sole input to the verdict.
        """
        result = _parse_test_output(1, PYTEST_ALL_GREEN)
        self.assertFalse(result["passed"])

    def test_zero_exit_beats_red_looking_text(self):
        """
        Symmetric case: output mentioning failures while the runner exits 0.
        Common when a test suite prints an expected-failure or a retried flake.
        Reporting this as a failure would burn iterations rewriting healthy code.
        """
        result = _parse_test_output(0, "1 failed, 3 passed\nretried, all green\n")
        self.assertTrue(result["passed"])

    def test_failure_recorded_only_when_failing(self):
        self.assertEqual(_parse_test_output(0, PYTEST_ALL_GREEN)["failures"], [])
        self.assertTrue(_parse_test_output(1, PYTEST_MIXED)["failures"])

    def test_failure_carries_output_tail_not_head(self):
        """
        Test runners put the summary and the failure list at the END of their
        output; the head is setup noise — dependency resolution, collection,
        banner text.  The critique shows only ~300 chars of this, so it must be
        the informative end.

        Uses output long enough that head and tail actually differ; a short log
        would pass this test no matter which end the parser kept.
        """
        noisy = ("Collecting dependencies from registry...\n" * 200) + PYTEST_MIXED
        error = _parse_test_output(1, noisy)["failures"][0]["error"]
        self.assertIn("2 failed, 3 passed", error)
        self.assertIn("FAILED tests/test_task.py::test_create", error)
        # The kept slice is literally the end of the output, and the setup
        # noise that dominates the log is almost entirely dropped.
        self.assertTrue(noisy.endswith(error))
        self.assertLess(error.count("Collecting dependencies"), 5)


class TestBestEffortCounts(unittest.TestCase):
    """Counts are cosmetic. Get them when cheap, omit them when not."""

    def test_pytest_failed_first_ordering(self):
        """
        pytest, jest and vitest all print FAILED BEFORE PASSED
        ("2 failed, 3 passed").  A pattern written as `passed.*?failed` matches
        none of the three most common runners in this stack.
        """
        result = _parse_test_output(1, PYTEST_MIXED)
        self.assertEqual(result["passed_count"], 3)
        self.assertEqual(result["failed_count"], 2)
        self.assertEqual(result["total"], 5)

    def test_pytest_all_green_has_no_failed_clause(self):
        """The green summary is just "3 passed in 0.02s" — no failure count."""
        result = _parse_test_output(0, PYTEST_ALL_GREEN)
        self.assertEqual(result["passed_count"], 3)
        self.assertEqual(result["failed_count"], 0)
        self.assertEqual(result["total"], 3)

    def test_jest(self):
        result = _parse_test_output(1, JEST_MIXED)
        self.assertEqual(result["passed_count"], 7)
        self.assertEqual(result["failed_count"], 1)

    def test_vitest_pipe_separator(self):
        result = _parse_test_output(1, VITEST_MIXED)
        self.assertEqual(result["passed_count"], 3)
        self.assertEqual(result["failed_count"], 1)

    def test_cargo(self):
        result = _parse_test_output(1, CARGO_MIXED)
        self.assertEqual(result["passed_count"], 3)
        self.assertEqual(result["failed_count"], 1)

    def test_surefire_sums_failures_and_errors(self):
        """Surefire separates assertion failures from thrown errors; both failed."""
        result = _parse_test_output(1, SUREFIRE_MIXED)
        self.assertEqual(result["total"], 6)
        self.assertEqual(result["failed_count"], 2)
        self.assertEqual(result["passed_count"], 4)

    def test_tap_counts_result_lines(self):
        result = _parse_test_output(1, TAP_MIXED)
        self.assertEqual(result["passed_count"], 3)
        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(result["total"], 4)

    def test_go_counts_package_verdicts(self):
        """go test reports per-package, so these are package counts, not tests."""
        result = _parse_test_output(1, GO_MIXED)
        self.assertEqual(result["passed_count"], 2)
        self.assertEqual(result["failed_count"], 1)


class TestFailsSafe(unittest.TestCase):
    """No match means no numbers — never a wrong number, never an exception."""

    def test_unknown_runner_omits_counts(self):
        """
        Adding Zig later is one line in the pattern list.  Until then it must
        degrade to "counts unavailable", not to a fabricated zero.
        """
        result = _parse_test_output(0, UNKNOWN_RUNNER)
        self.assertTrue(result["passed"])
        self.assertNotIn("total", result)
        self.assertNotIn("passed_count", result)
        self.assertNotIn("failed_count", result)

    def test_empty_output(self):
        result = _parse_test_output(1, "")
        self.assertFalse(result["passed"])
        self.assertNotIn("total", result)

    def test_binary_garbage_does_not_raise(self):
        result = _parse_test_output(1, "\x00\xff�" * 500)
        self.assertFalse(result["passed"])

    def test_huge_output_does_not_raise(self):
        result = _parse_test_output(1, "noise line\n" * 50000)
        self.assertFalse(result["passed"])

    def test_counts_never_cross_lines(self):
        """
        A log where "passed" and "failed" appear on unrelated lines must not be
        stitched into a bogus pair.  Patterns stay within a single line.
        """
        stitched = "Deploy step 4 passed\n...\nlint: 9 failed\n"
        result = _parse_test_output(1, stitched)
        self.assertNotIn("failed_count", result)

    def test_module_reaches_for_no_model_at_all(self):
        """
        The point of the change: nothing in test_tools may call a model.  Not
        just the parser — the whole module, because relocating the call rather
        than deleting it preserves the crash class.  If a future edit
        reintroduces one, this fails loudly.
        """
        import inspect

        from src.llamaindex_crew.tools import test_tools

        source = inspect.getsource(test_tools)
        for forbidden in ("get_llm_for_agent", "llm_config", "llm.complete"):
            self.assertNotIn(forbidden, source)

    def test_llm_parser_is_gone(self):
        """The LLM path is deleted, not relocated behind a flag."""
        from src.llamaindex_crew.tools import test_tools

        self.assertFalse(hasattr(test_tools, "_parse_test_output_with_llm"))


if __name__ == "__main__":
    unittest.main()
