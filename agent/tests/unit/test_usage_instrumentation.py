"""Regression guards for the token/cost instrumentation that silently recorded nothing.

Two independent defects made `llm_usage` and `tool_usage` stay empty:

1. ``SoftwareDevWorkflow`` never bound its budget tracker to the job, so
   ``project_id`` stayed ``"default-project"`` and both DB writes — which are
   guarded on that value — were skipped for the entire pipeline.
2. ``BaseLlamaIndexAgent.chat_simple`` called ``record_usage`` with
   ``prompt_tokens=``/``completion_tokens=`` keywords that do not exist in the
   signature; the resulting TypeError was swallowed by a bare ``except``.
"""
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from llamaindex_crew.budget.tracker import BudgetTracker


def test_record_usage_signature_matches_call_sites():
    """chat_simple/chat must call record_usage with parameters that actually exist."""
    params = set(inspect.signature(BudgetTracker.record_usage).parameters) - {"self"}
    assert params == {
        "project_id", "agent_name", "model", "input_tokens", "output_tokens",
    }

    source = (
        Path(__file__).parent.parent.parent
        / "src/llamaindex_crew/agents/base_agent.py"
    ).read_text(encoding="utf-8")

    # The old, broken keywords must not reappear anywhere.
    assert "prompt_tokens=" not in source
    assert "completion_tokens=" not in source


def test_record_usage_accepts_the_arguments_base_agent_passes():
    """Calling with exactly base_agent's keyword set must not raise."""
    tracker = BudgetTracker()
    tracker.job_db = None  # no DB side effect in this test
    result = tracker.record_usage(
        project_id="job-123",
        agent_name="Developer",
        model="gpt-4o-mini",
        input_tokens=100,
        output_tokens=50,
    )
    assert "cost" in result


def test_workflow_binds_tracker_to_job_id():
    """The pipeline's tracker must carry the job id, else every usage row is dropped."""
    source = (
        Path(__file__).parent.parent.parent
        / "src/llamaindex_crew/workflows/software_dev_workflow.py"
    ).read_text(encoding="utf-8")

    assert "self.budget_tracker.project_id = project_id" in source, (
        "SoftwareDevWorkflow must bind budget_tracker.project_id to the job id; "
        "without it llm_usage/tool_usage writes are skipped as 'default-project'."
    )


def test_unbound_tracker_falls_back_to_env_project_id(monkeypatch):
    """An unbound tracker takes PROJECT_ID (default 'default-project') — the value
    the DB-write guard rejects, which is why binding it per job matters."""
    monkeypatch.delenv("PROJECT_ID", raising=False)
    assert BudgetTracker().project_id == "default-project"

    monkeypatch.setenv("PROJECT_ID", "some-env-project")
    assert BudgetTracker().project_id == "some-env-project"
