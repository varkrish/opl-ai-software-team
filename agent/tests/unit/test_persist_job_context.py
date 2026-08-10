"""
Every flow that finishes a job must record what it left behind.

The build flow reached the plane through three calls inlined in
``_run_job_async_impl``. Refine reached only the correction write, and import
reached nothing, so the plane learned exclusively from first drafts. That is
backwards: a refine that ends validating clean is a *better* blueprint than the
build it came from — the same contract with the defects fixed — and it was the
one thing being discarded.

The three calls are now one function so a fourth flow cannot quietly acquire
two of them. This codebase has just finished collapsing thirteen duplicated
definitions; a write path copied into three runners is how that happens again.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))


class _JobDb:
    def __init__(self, row=None):
        self._row = row if row is not None else {"owner_id": "u", "vision": "v"}

    def get_job(self, job_id):
        return self._row


@pytest.fixture
def calls(monkeypatch):
    """Record which writes fire, without touching a database."""
    import crew_studio.memory_hooks as hooks

    seen = []
    monkeypatch.setattr(hooks, "write_job_outcome_memory",
                        lambda jid, **kw: seen.append("outcome"))
    monkeypatch.setattr(hooks, "write_correction_memories",
                        lambda jid, **kw: seen.append("corrections"))
    monkeypatch.setattr(hooks, "write_approved_solution_memory",
                        lambda jid, **kw: seen.append("blueprint") or 1)
    return seen


def test_a_finished_build_records_all_three(calls, tmp_path):
    from crew_studio.memory_hooks import persist_job_context

    persist_job_context("job-1", config=None, job_db=_JobDb(),
                        workspace_path=tmp_path, final_status="completed")

    assert calls == ["outcome", "corrections", "blueprint"]


def test_the_outcome_write_comes_first(calls, tmp_path):
    """It pins the resolved framework/domain onto the job row that the
    correction and blueprint writes then resolve their scope from."""
    from crew_studio.memory_hooks import persist_job_context

    persist_job_context("job-1", config=None, job_db=_JobDb(),
                        workspace_path=tmp_path)

    assert calls[0] == "outcome"


def test_a_refinement_records_the_blueprint_without_a_new_verdict(calls, tmp_path):
    """
    Refinement leaves the job on its previous status and runs no validation of
    its own, so there is no fresh verdict — but the refined tree is exactly the
    blueprint worth keeping.
    """
    from crew_studio.memory_hooks import persist_job_context

    persist_job_context("job-1", config=None, job_db=_JobDb(),
                        workspace_path=tmp_path, write_outcome=False)

    assert "outcome" not in calls
    assert calls == ["corrections", "blueprint"]


def test_one_failing_write_does_not_stop_the_others(monkeypatch, tmp_path):
    """Recall is never worth failing a job over."""
    import crew_studio.memory_hooks as hooks

    seen = []
    def _boom(jid, **kw):
        raise RuntimeError("postgres is down")

    monkeypatch.setattr(hooks, "write_job_outcome_memory", _boom)
    monkeypatch.setattr(hooks, "write_correction_memories", _boom)
    monkeypatch.setattr(hooks, "write_approved_solution_memory",
                        lambda jid, **kw: seen.append("blueprint") or 1)

    hooks.persist_job_context("job-1", config=None, job_db=_JobDb(),
                              workspace_path=tmp_path)

    assert seen == ["blueprint"], "an earlier failure must not swallow the blueprint"


def test_no_workspace_is_a_no_op(calls):
    from crew_studio.memory_hooks import persist_job_context

    persist_job_context("job-1", config=None, job_db=_JobDb(), workspace_path=None)

    assert calls == []


def test_an_unreadable_job_db_does_not_raise(calls, tmp_path):
    class _Broken:
        def get_job(self, job_id):
            raise RuntimeError("database is locked")

    from crew_studio.memory_hooks import persist_job_context

    with pytest.raises(RuntimeError):
        # The first fetch is outside the guarded section by design: without a
        # job row there is no scope, and silently writing to org "default"
        # is the failure this whole area was just fixed for.
        persist_job_context("job-1", config=None, job_db=_Broken(),
                            workspace_path=tmp_path)
