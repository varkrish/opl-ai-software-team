"""
Tests for memory summary generation.

The load-bearing property: a summary is ALWAYS produced. If the utility model is
unreachable or returns junk, the deterministic template still carries the
framework, status, and failure counts that make recall useful. Losing the summary
because a cheap model timed out would be worse than a plain one.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

_AGENT_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(_AGENT_SRC))

from llamaindex_crew.memory.summaries import (  # noqa: E402
    _validation_counts,
    build_jira_context_summary,
    build_jira_epic_summary,
    collect_job_outcome_signals,
    summarize_job_outcome,
    summarize_reference_doc,
)


class _FakeDB:
    """Minimal JobDatabase stand-in."""

    def __init__(self, *, refinements=0, failed_issues=0, usage=None, raises=False):
        self._refinements = refinements
        self._failed = failed_issues
        self._usage = usage or []
        self._raises = raises

    def get_refinement_history(self, job_id):
        if self._raises:
            raise RuntimeError("db down")
        return [{"id": i} for i in range(self._refinements)]

    def get_failed_validation_issues(self, job_id):
        if self._raises:
            raise RuntimeError("db down")
        return [{"id": i} for i in range(self._failed)]

    def get_llm_usage(self, job_id):
        if self._raises:
            raise RuntimeError("db down")
        return self._usage


class TestValidationCounts(unittest.TestCase):
    def test_dict_shaped_checks(self):
        counts = _validation_counts(
            {"checks": {"a": {"pass": True}, "b": {"pass": False}}, "overall": "ISSUES_FOUND"}
        )
        self.assertEqual(counts["total"], 2)
        self.assertEqual(counts["passed"], 1)
        self.assertEqual(counts["failed_names"], ["b"])
        self.assertEqual(counts["overall"], "ISSUES_FOUND")

    def test_list_shaped_checks(self):
        counts = _validation_counts(
            {"checks": [{"name": "x", "pass": True}, {"name": "y", "pass": False}]}
        )
        self.assertEqual((counts["total"], counts["passed"]), (2, 1))
        self.assertEqual(counts["failed_names"], ["y"])

    def test_status_string_instead_of_pass_flag(self):
        counts = _validation_counts(
            {"checks": {"a": {"status": "passed"}, "b": {"status": "failed"}}}
        )
        self.assertEqual((counts["total"], counts["passed"]), (2, 1))

    def test_empty_report(self):
        self.assertEqual(_validation_counts(None)["total"], 0)
        self.assertEqual(_validation_counts({})["total"], 0)

    def test_failed_names_capped(self):
        checks = {f"c{i}": {"pass": False} for i in range(20)}
        self.assertEqual(len(_validation_counts({"checks": checks})["failed_names"]), 5)


class TestCollectSignals(unittest.TestCase):
    def test_reads_validation_report_from_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "validation_report.json").write_text(
                json.dumps({"checks": {"a": {"pass": True}, "b": {"pass": False}}}),
                encoding="utf-8",
            )
            signals = collect_job_outcome_signals(
                "job-1", job={"vision": "Build it"}, workspace_path=workspace
            )
            self.assertEqual(signals["validation_total"], 2)
            self.assertEqual(signals["validation_passed"], 1)

    def test_results_report_preferred_over_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "validation_report.json").write_text(
                json.dumps({"checks": {"a": {"pass": False}}}), encoding="utf-8"
            )
            signals = collect_job_outcome_signals(
                "job-1",
                workspace_path=workspace,
                results={"validation_report": {"checks": {"x": {"pass": True}}}},
            )
            self.assertEqual(signals["validation_passed"], 1)
            self.assertEqual(signals["validation_total"], 1)

    def test_aggregates_db_signals(self):
        db = _FakeDB(
            refinements=2,
            failed_issues=3,
            usage=[{"input_tokens": 100, "output_tokens": 50}],
        )
        signals = collect_job_outcome_signals("job-1", job_db=db)
        self.assertEqual(signals["refinement_count"], 2)
        self.assertEqual(signals["failed_issue_count"], 3)
        self.assertEqual(signals["total_tokens"], 150)

    def test_db_errors_do_not_propagate(self):
        # This runs in a post-job hook; a DB hiccup must not fail the job.
        signals = collect_job_outcome_signals("job-1", job_db=_FakeDB(raises=True))
        self.assertEqual(signals["refinement_count"], 0)
        self.assertEqual(signals["total_tokens"], 0)

    def test_solution_spec_head_captured(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "solution_spec.md").write_text(
                "# Solution\nUse Frappe DocTypes.", encoding="utf-8"
            )
            signals = collect_job_outcome_signals("job-1", workspace_path=workspace)
            self.assertIn("Frappe", signals["solution_spec_head"])

    def test_vision_truncated(self):
        signals = collect_job_outcome_signals("job-1", job={"vision": "x" * 5000})
        self.assertLessEqual(len(signals["vision"]), 500)

    def test_metadata_json_string_parsed(self):
        signals = collect_job_outcome_signals(
            "job-1", job={"metadata": '{"solutioning_stats": {"pass_count": 2}}'}
        )
        self.assertEqual(signals["solutioning_passes"], 2)

    def test_no_inputs_at_all_still_returns_signals(self):
        signals = collect_job_outcome_signals("job-1")
        self.assertEqual(signals["job_id"], "job-1")
        self.assertEqual(signals["final_status"], "unknown")


class TestSummarizeJobOutcome(unittest.TestCase):
    def _signals(self, **overrides):
        base = collect_job_outcome_signals(
            "job-1",
            job={"vision": "Build asset depreciation"},
            results={
                "status": "completed",
                "validation_report": {"checks": {"a": {"pass": True}, "b": {"pass": False}}},
            },
            final_status="completed",
        )
        base.update(overrides)
        return base

    def test_uses_llm_when_available(self):
        summary = summarize_job_outcome(
            self._signals(), scope_label="frappe / asset", llm_call=lambda p: "LLM says hi."
        )
        self.assertEqual(summary, "LLM says hi.")

    def test_llm_receives_the_key_facts(self):
        captured = {}

        def fake_llm(prompt):
            captured["prompt"] = prompt
            return "ok"

        summarize_job_outcome(
            self._signals(refinement_count=3), scope_label="frappe / asset", llm_call=fake_llm
        )
        self.assertIn("frappe / asset", captured["prompt"])
        self.assertIn("completed", captured["prompt"])
        self.assertIn("3", captured["prompt"])

    def test_falls_back_to_template_when_llm_raises(self):
        def boom(_prompt):
            raise RuntimeError("model unreachable")

        summary = summarize_job_outcome(
            self._signals(), scope_label="frappe / asset", llm_call=boom
        )
        self.assertTrue(summary)
        self.assertIn("frappe / asset", summary)
        self.assertIn("completed", summary)

    def test_falls_back_when_llm_returns_blank(self):
        summary = summarize_job_outcome(
            self._signals(), scope_label="frappe / asset", llm_call=lambda p: "   "
        )
        self.assertTrue(summary)
        self.assertIn("frappe / asset", summary)

    def test_template_summary_carries_failure_detail(self):
        summary = summarize_job_outcome(
            self._signals(failed_issue_count=4, refinement_count=2),
            scope_label="frappe / asset",
        )
        self.assertIn("1/2 checks passed", summary)
        self.assertIn("4 unresolved", summary)
        self.assertIn("2 human refinement", summary)

    def test_no_llm_still_produces_summary(self):
        self.assertTrue(summarize_job_outcome(self._signals(), scope_label="x / y"))

    def test_respects_max_chars(self):
        summary = summarize_job_outcome(
            self._signals(), scope_label="x / y", llm_call=lambda p: "z" * 5000, max_chars=100
        )
        self.assertLessEqual(len(summary), 100)


class TestSummarizeReferenceDoc(unittest.TestCase):
    def test_llm_summary_is_prefixed_with_filename(self):
        summary = summarize_reference_doc(
            "mta-report.md", "47 Java EE issues found", llm_call=lambda p: "MTA report."
        )
        self.assertIn("mta-report.md", summary)
        self.assertIn("MTA report.", summary)

    def test_falls_back_to_leading_lines(self):
        summary = summarize_reference_doc(
            "notes.md", "First line\nSecond line\nThird line\nFourth"
        )
        self.assertIn("notes.md", summary)
        self.assertIn("First line", summary)

    def test_empty_excerpt_returns_empty(self):
        # Storing a filename with no information in it is worse than storing nothing.
        self.assertEqual(summarize_reference_doc("x.md", ""), "")
        self.assertEqual(summarize_reference_doc("x.md", "   "), "")

    def test_llm_failure_falls_back(self):
        def boom(_p):
            raise RuntimeError("nope")

        summary = summarize_reference_doc("x.md", "Content here", llm_call=boom)
        self.assertIn("Content here", summary)


class TestJiraSummaries(unittest.TestCase):
    def test_context_summary_includes_all_fields(self):
        summary = build_jira_context_summary(
            "ASSET-42",
            "Add depreciation workflow",
            issue_type="Story",
            mode="build",
            repo_url="https://github.com/acme/erp",
            has_gherkin=True,
        )
        self.assertIn("ASSET-42", summary)
        self.assertIn("Story", summary)
        self.assertIn("build", summary)
        self.assertIn("acme/erp", summary)
        self.assertIn("Gherkin provided: yes", summary)

    def test_context_summary_reports_missing_repo(self):
        summary = build_jira_context_summary("ASSET-1", "Do a thing")
        self.assertIn("Repo: none", summary)
        self.assertIn("Gherkin provided: no", summary)

    def test_epic_summary_lists_children(self):
        summary = build_jira_epic_summary(
            "ASSET-1", "Asset management", ["ASSET-2", "ASSET-3"]
        )
        self.assertIn("ASSET-1", summary)
        self.assertIn("ASSET-2, ASSET-3", summary)

    def test_epic_summary_with_no_children(self):
        self.assertIn(
            "none linked", build_jira_epic_summary("ASSET-1", "Asset management", [])
        )


if __name__ == "__main__":
    unittest.main()
