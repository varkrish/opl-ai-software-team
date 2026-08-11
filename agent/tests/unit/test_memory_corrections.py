"""
Corrections are the highest-signal learning substrate the system produces, and
they exist in every job mode — not just refine.

When a human rejects a plan, rewrites a solution spec, or tells the agent what
it got wrong, they state the problem in plain language. The same is true of
machine critique: a test-bed critique or a solution-critique pass says exactly
what was insufficient. Before this module all of that was either counted
(``refinement_count: 2``) or left on disk unread, so the memory plane could
recall *that* a job struggled but never *what anyone had to fix*.

Every mode contributes something:

    build/greenfield  plan review feedback, solution review feedback,
                      solution_critique_pass_N.json, loop test critique
    import / fix      the auto-fix instruction (job vision) + refinements
    refine            refinement prompt paired with the agent's response
    migration         migration issue description + hint
    refactor          per-file refactor instruction

These tests are written against the collector's contract, not its
implementation: what it gathers, how it degrades, and what it refuses to store.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from llamaindex_crew.memory.corrections import (  # noqa: E402
    Correction,
    collect_corrections,
    render_correction,
)


class _FakeDB:
    """Minimal JobDatabase stand-in; every getter is independently riggable."""

    def __init__(self, *, refinements=None, migration=None, refactor=None, raises=()):
        self._refinements = refinements or []
        self._migration = migration or []
        self._refactor = refactor or []
        self._raises = set(raises)

    def _maybe_raise(self, name):
        if name in self._raises:
            raise RuntimeError(f"{name} exploded")

    def get_refinement_history(self, job_id, limit=20):
        self._maybe_raise("get_refinement_history")
        return list(self._refinements)

    def get_migration_issues(self, job_id):
        self._maybe_raise("get_migration_issues")
        return list(self._migration)

    def get_refactor_tasks(self, job_id):
        self._maybe_raise("get_refactor_tasks")
        return list(self._refactor)


def _job(mode="build", **metadata):
    meta = {"job_mode": mode} if mode else {}
    meta.update(metadata)
    return {"id": "job-1", "vision": "Build asset depreciation", "metadata": meta}


# ── Human feedback: build / greenfield ───────────────────────────────────────


def test_plan_review_feedback_is_collected_verbatim():
    job = _job(plan_feedback_history=[{"feedback": "Do not use MongoDB, we are a Postgres shop", "at": "t"}])
    corrections = collect_corrections("job-1", job=job)

    assert len(corrections) == 1
    assert corrections[0].source == "plan_review"
    assert corrections[0].instruction == "Do not use MongoDB, we are a Postgres shop"


def test_solution_review_feedback_is_collected_verbatim():
    job = _job(solution_feedback_history=[{"feedback": "Split the billing service out", "at": "t"}])
    corrections = collect_corrections("job-1", job=job)

    assert [c.source for c in corrections] == ["solution_review"]
    assert corrections[0].instruction == "Split the billing service out"


def test_multiple_feedback_rounds_all_captured():
    job = _job(
        plan_feedback_history=[{"feedback": "first"}, {"feedback": "second"}],
        solution_feedback_history=[{"feedback": "third"}],
    )
    assert len(collect_corrections("job-1", job=job)) == 3


def test_double_encoded_timestamp_is_normalised():
    # software_dev_workflow wrapped an already-serialised isoformat in
    # json.dumps, so historical rows carry '"2026-08-09T..."' with literal
    # quotes. Old rows must not leak quoted timestamps into memory.
    job = _job(plan_feedback_history=[{"feedback": "x", "at": '"2026-08-09T10:00:00+00:00"'}])
    assert collect_corrections("job-1", job=job)[0].at == "2026-08-09T10:00:00+00:00"


def test_blank_feedback_entries_are_dropped():
    job = _job(plan_feedback_history=[{"feedback": "   "}, {"feedback": ""}, {}])
    assert collect_corrections("job-1", job=job) == []


# ── Refine mode: instruction paired with response ────────────────────────────


def test_refinement_prompt_and_response_are_paired():
    db = _FakeDB(refinements=[{
        "prompt": "The invoice total ignores tax",
        "response": "Added tax calculation to InvoiceService.total()",
        "status": "completed",
        "file_path": "services/invoice.py",
        "created_at": "t",
    }])
    corrections = collect_corrections("job-1", job=_job("refine"), job_db=db)

    assert len(corrections) == 1
    assert corrections[0].source == "refinement"
    assert corrections[0].instruction == "The invoice total ignores tax"
    assert corrections[0].response == "Added tax calculation to InvoiceService.total()"
    assert corrections[0].file_path == "services/invoice.py"


def test_refinement_without_response_still_collected():
    # Older rows predate the response column; the human instruction alone is
    # still worth recalling.
    db = _FakeDB(refinements=[{"prompt": "Fix the totals", "status": "completed"}])
    corrections = collect_corrections("job-1", job=_job("refine"), job_db=db)

    assert len(corrections) == 1
    assert corrections[0].response == ""


def test_failed_refinement_is_captured_as_negative_signal():
    # What the agent could NOT do is as instructive as what it did.
    db = _FakeDB(refinements=[{
        "prompt": "Rewrite the auth module in Rust",
        "status": "failed",
        "error": "The AI agent completed but did not modify any files.",
    }])
    corrections = collect_corrections("job-1", job=_job("refine"), job_db=db)

    assert corrections[0].outcome == "failed"
    assert "did not modify" in corrections[0].response


def test_running_refinement_is_skipped():
    # Mid-flight refinements have no outcome yet.
    db = _FakeDB(refinements=[{"prompt": "in progress", "status": "running"}])
    assert collect_corrections("job-1", job=_job("refine"), job_db=db) == []


# ── Machine critique: build mode ─────────────────────────────────────────────


def test_solution_critique_passes_are_read_from_workspace(tmp_path):
    (tmp_path / "solution_critique_pass_1.json").write_text(
        json.dumps({"approved": False, "must_fix": ["No auth story", "Missing rate limits"]}),
        encoding="utf-8",
    )
    corrections = collect_corrections("job-1", job=_job(), workspace_path=tmp_path)

    assert len(corrections) == 1
    assert corrections[0].source == "solution_critique"
    assert "No auth story" in corrections[0].instruction


def test_approved_critique_pass_is_skipped(tmp_path):
    (tmp_path / "solution_critique_pass_2.json").write_text(
        json.dumps({"approved": True, "must_fix": []}), encoding="utf-8"
    )
    assert collect_corrections("job-1", job=_job(), workspace_path=tmp_path) == []


def test_critique_passes_are_ordered_by_pass_number(tmp_path):
    for n, issue in ((10, "tenth"), (2, "second"), (1, "first")):
        (tmp_path / f"solution_critique_pass_{n}.json").write_text(
            json.dumps({"approved": False, "must_fix": [issue]}), encoding="utf-8"
        )
    order = [c.instruction for c in collect_corrections("job-1", job=_job(), workspace_path=tmp_path)]

    # Lexical globbing would put pass_10 before pass_2.
    assert order[0].endswith("first") or "first" in order[0]
    assert "second" in order[1]
    assert "tenth" in order[2]


def test_malformed_critique_file_is_skipped(tmp_path):
    (tmp_path / "solution_critique_pass_1.json").write_text("{not json", encoding="utf-8")
    assert collect_corrections("job-1", job=_job(), workspace_path=tmp_path) == []


def test_loop_test_critique_is_collected():
    job = _job(loop_state={"current_critique": "Tests fail: expected 200 got 500 on /invoices"})
    corrections = collect_corrections("job-1", job=job)

    assert [c.source for c in corrections] == ["test_critique"]
    assert "expected 200 got 500" in corrections[0].instruction


# ── Migration and refactor modes ─────────────────────────────────────────────


def test_migration_issues_are_collected():
    db = _FakeDB(migration=[{
        "title": "Javax to Jakarta",
        "description": "javax.persistence imports must move to jakarta.persistence",
        "migration_hint": "Use the openrewrite recipe",
        "severity": "mandatory",
    }])
    corrections = collect_corrections("job-1", job=_job("migration"), job_db=db)

    assert corrections[0].source == "migration_issue"
    assert "jakarta.persistence" in corrections[0].instruction
    assert "openrewrite" in corrections[0].response


def test_refactor_instructions_are_collected():
    db = _FakeDB(refactor=[{
        "file_path": "src/Legacy.java",
        "instruction": "Extract the payment logic into its own service",
        "action": "extract",
    }])
    corrections = collect_corrections("job-1", job=_job("refactor"), job_db=db)

    assert corrections[0].source == "refactor_task"
    assert corrections[0].file_path == "src/Legacy.java"


def test_import_mode_auto_fix_instruction_is_collected():
    # Import/fix jobs put the human's instruction in the vision and drive it
    # through a refinement, so it must not be missed just because mode != refine.
    db = _FakeDB(refinements=[{
        "prompt": "Fix all the MTA findings in the auth module",
        "response": "Updated 4 files",
        "status": "completed",
    }])
    corrections = collect_corrections("job-1", job=_job("import"), job_db=db)

    assert len(corrections) == 1
    assert corrections[0].mode == "import"


# ── Cross-mode behaviour ─────────────────────────────────────────────────────


def test_all_modes_collected_together_when_data_coexists(tmp_path):
    (tmp_path / "solution_critique_pass_1.json").write_text(
        json.dumps({"approved": False, "must_fix": ["gap"]}), encoding="utf-8"
    )
    job = _job(
        "build",
        plan_feedback_history=[{"feedback": "plan wrong"}],
        solution_feedback_history=[{"feedback": "solution wrong"}],
        loop_state={"current_critique": "tests red"},
    )
    db = _FakeDB(
        refinements=[{"prompt": "fix it", "status": "completed"}],
        migration=[{"title": "t", "description": "mig", "migration_hint": ""}],
        refactor=[{"file_path": "f", "instruction": "refac"}],
    )
    sources = {c.source for c in collect_corrections("job-1", job=job, workspace_path=tmp_path, job_db=db)}

    assert sources == {
        "plan_review", "solution_review", "solution_critique",
        "test_critique", "refinement", "migration_issue", "refactor_task",
    }


def test_mode_is_stamped_on_every_correction():
    job = _job("migration", plan_feedback_history=[{"feedback": "x"}])
    assert all(c.mode == "migration" for c in collect_corrections("job-1", job=job))


def test_no_sources_yields_no_corrections():
    assert collect_corrections("job-1", job=_job()) == []


def test_missing_job_and_db_is_tolerated():
    assert collect_corrections("job-1") == []


def test_metadata_json_string_is_parsed():
    job = {"metadata": json.dumps({"plan_feedback_history": [{"feedback": "from json string"}]})}
    assert collect_corrections("job-1", job=job)[0].instruction == "from json string"


# ── Resilience: this runs in a post-job hook ─────────────────────────────────


@pytest.mark.parametrize(
    "failing", ["get_refinement_history", "get_migration_issues", "get_refactor_tasks"]
)
def test_db_failure_in_one_source_does_not_lose_the_others(failing):
    db = _FakeDB(
        refinements=[{"prompt": "r", "status": "completed"}],
        migration=[{"title": "t", "description": "m", "migration_hint": ""}],
        refactor=[{"file_path": "f", "instruction": "i"}],
        raises=[failing],
    )
    corrections = collect_corrections("job-1", job=_job(), job_db=db)

    # Two of the three survive; the collector never raises.
    assert len(corrections) == 2


def test_unreadable_workspace_is_tolerated(tmp_path):
    missing = tmp_path / "does-not-exist"
    assert collect_corrections("job-1", job=_job(), workspace_path=missing) == []


def test_corrections_are_capped(tmp_path):
    job = _job(plan_feedback_history=[{"feedback": f"item {i}"} for i in range(200)])
    assert len(collect_corrections("job-1", job=job, limit=25)) == 25


def test_very_long_instruction_is_truncated():
    job = _job(plan_feedback_history=[{"feedback": "x" * 10_000}])
    assert len(collect_corrections("job-1", job=job)[0].instruction) <= 2000


# ── Rendering for storage ────────────────────────────────────────────────────


def test_render_includes_instruction_and_response():
    correction = Correction(
        source="refinement", mode="refine",
        instruction="The invoice total ignores tax",
        response="Added tax to InvoiceService.total()",
        file_path="services/invoice.py",
    )
    text = render_correction(correction)

    assert "invoice total ignores tax" in text
    assert "Added tax" in text
    assert "services/invoice.py" in text


def test_render_labels_the_source_in_plain_language():
    text = render_correction(Correction(source="plan_review", mode="build", instruction="no mongo"))
    assert "plan review" in text.lower()


def test_render_without_response_is_still_useful():
    text = render_correction(Correction(source="plan_review", mode="build", instruction="no mongo"))
    assert "no mongo" in text
    assert text.strip()


def test_render_marks_failed_outcomes():
    text = render_correction(
        Correction(source="refinement", mode="refine", instruction="do x",
                   response="could not", outcome="failed")
    )
    assert "failed" in text.lower()


def test_render_is_bounded():
    text = render_correction(
        Correction(source="refinement", mode="refine", instruction="i" * 5000, response="r" * 5000),
        max_chars=500,
    )
    assert len(text) <= 500


# ── Write hook ───────────────────────────────────────────────────────────────

import types  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))


class _Recorder:
    """Captures what reaches the MemMachine client."""

    def __init__(self):
        self.handles = []
        self.writes = []


def _install_fake_client(recorder):
    module = types.ModuleType("memmachine_client")

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def get_or_create_project(self, **kwargs):
            pass

        def close(self):
            pass

    class FakeMemory:
        def __init__(self, client, org_id, project_id, metadata=None, **kwargs):
            recorder.handles.append((org_id, project_id, metadata))

        def add(self, content, **kwargs):
            recorder.writes.append({"content": content, "metadata": kwargs.get("metadata", {})})
            return [{"uid": "1"}]

    module.MemMachineClient = FakeClient
    module.Memory = FakeMemory
    sys.modules["memmachine_client"] = module


@pytest.fixture()
def recorder():
    rec = _Recorder()
    saved = sys.modules.get("memmachine_client")
    _install_fake_client(rec)
    yield rec
    if saved is None:
        sys.modules.pop("memmachine_client", None)
    else:
        sys.modules["memmachine_client"] = saved


class _MemCfg:
    def __init__(self, **kw):
        self.enabled = kw.get("enabled", True)
        self.base_url = kw.get("base_url", "http://memmachine-app:8080")
        self.api_key = None
        self.timeout_seconds = 15
        self.default_org_id = "default"
        self.default_project_id = "unknown-framework"
        self.shared_project_id = "shared-context"
        self.write_job_outcome = True
        self.write_reference_docs = True
        self.write_corrections = kw.get("write_corrections", True)
        self.read_at_solutioning = True
        self.search_limit = 5
        self.search_score_threshold = None
        self.max_recall_chars = 4000
        self.summary_max_chars = 1200
        self.summary_agent_type = "none"
        self.max_corrections_per_job = kw.get("max_corrections_per_job", 25)


class _Cfg:
    def __init__(self, **kw):
        self.memory = _MemCfg(**kw)


def _hook():
    from crew_studio.memory_hooks import write_correction_memories

    return write_correction_memories


def test_hook_writes_one_episode_per_correction(recorder):
    job = {"team_id": "acme", "metadata": {
        "framework": "frappe-15", "job_mode": "build",
        "plan_feedback_history": [{"feedback": "no mongo"}, {"feedback": "use keycloak"}],
    }}
    written = _hook()("job-1", config=_Cfg(), job=job)

    assert written == 2
    assert len(recorder.writes) == 2
    assert any("no mongo" in w["content"] for w in recorder.writes)


def test_hook_uses_the_framework_project(recorder):
    # Corrections are stack-specific: "on Frappe, always X" only applies to
    # Frappe. They must not land in the framework-agnostic shared project.
    job = {"team_id": "acme", "metadata": {
        "framework": "frappe-15", "plan_feedback_history": [{"feedback": "x"}],
    }}
    _hook()("job-1", config=_Cfg(), job=job)

    assert recorder.handles[0][1] == "frappe-15"


def test_hook_tags_episodes_for_filtered_recall(recorder):
    job = {"team_id": "acme", "metadata": {
        "framework": "frappe", "job_mode": "refine",
        "plan_feedback_history": [{"feedback": "x"}],
    }}
    _hook()("job-1", config=_Cfg(), job=job)

    metadata = recorder.writes[0]["metadata"]
    assert metadata["type"] == "correction"
    assert metadata["correction_source"] == "plan_review"
    assert metadata["job_id"] == "job-1"


def test_hook_is_a_noop_when_memory_disabled(recorder):
    job = {"metadata": {"plan_feedback_history": [{"feedback": "x"}]}}
    assert _hook()("job-1", config=_Cfg(enabled=False), job=job) == 0
    assert recorder.writes == []


def test_hook_respects_write_corrections_switch(recorder):
    job = {"metadata": {"plan_feedback_history": [{"feedback": "x"}]}}
    assert _hook()("job-1", config=_Cfg(write_corrections=False), job=job) == 0


def test_hook_writes_nothing_when_there_are_no_corrections(recorder):
    assert _hook()("job-1", config=_Cfg(), job={"metadata": {}}) == 0
    assert recorder.writes == []


def test_hook_caps_writes(recorder):
    job = {"metadata": {"plan_feedback_history": [{"feedback": f"f{i}"} for i in range(50)]}}
    written = _hook()("job-1", config=_Cfg(max_corrections_per_job=5), job=job)

    assert written == 5


def test_hook_never_raises_on_collector_failure(recorder):
    # A malformed job row must not fail the job it is summarising.
    class Exploding:
        def get_refinement_history(self, *a, **k):
            raise RuntimeError("db gone")

        def get_migration_issues(self, *a, **k):
            raise RuntimeError("db gone")

        def get_refactor_tasks(self, *a, **k):
            raise RuntimeError("db gone")

    job = {"metadata": {"plan_feedback_history": [{"feedback": "survives"}]}}
    assert _hook()("job-1", config=_Cfg(), job=job, job_db=Exploding()) == 1


def test_hook_tolerates_no_config(recorder):
    assert _hook()("job-1", config=None, job={"metadata": {}}) == 0


# ── Source filtering + the post-refinement seam ──────────────────────────────
#
# Refinements are issued AFTER a job reaches a terminal state, so the post-job
# hook has already run by the time one exists. Without a seam at refinement
# completion the single richest correction source would never be recorded.


def test_sources_filter_restricts_what_is_collected():
    db = _FakeDB(refinements=[{"prompt": "fix it", "status": "completed"}])
    job = _job("build", plan_feedback_history=[{"feedback": "plan wrong"}])

    only = collect_corrections("job-1", job=job, job_db=db, sources={"refinement"})
    assert [c.source for c in only] == ["refinement"]


def test_sources_filter_none_means_everything():
    db = _FakeDB(refinements=[{"prompt": "fix it", "status": "completed"}])
    job = _job("build", plan_feedback_history=[{"feedback": "plan wrong"}])

    assert len(collect_corrections("job-1", job=job, job_db=db, sources=None)) == 2


def test_sources_filter_skips_db_calls_it_does_not_need():
    # Filtering must not pay for sources it will discard, and must not blow up
    # when an unrelated source is broken.
    db = _FakeDB(
        refinements=[{"prompt": "fix it", "status": "completed"}],
        raises=["get_migration_issues", "get_refactor_tasks"],
    )
    only = collect_corrections("job-1", job=_job(), job_db=db, sources={"refinement"})
    assert len(only) == 1


def test_refinement_hook_writes_only_the_newest_refinement(recorder):
    from crew_studio.memory_hooks import write_refinement_correction_memory

    # get_refinement_history returns newest-first.
    db = _FakeDB(refinements=[
        {"prompt": "newest", "response": "did it", "status": "completed"},
        {"prompt": "older", "response": "did that", "status": "completed"},
    ])
    job = {"team_id": "acme", "metadata": {"framework": "frappe", "job_mode": "refine"}}
    written = write_refinement_correction_memory("job-1", config=_Cfg(), job=job, job_db=db)

    assert written == 1
    assert "newest" in recorder.writes[0]["content"]
    assert "older" not in recorder.writes[0]["content"]


def test_refinement_hook_is_a_noop_when_disabled(recorder):
    from crew_studio.memory_hooks import write_refinement_correction_memory

    db = _FakeDB(refinements=[{"prompt": "x", "status": "completed"}])
    assert write_refinement_correction_memory(
        "job-1", config=_Cfg(enabled=False), job={}, job_db=db
    ) == 0


def test_refinement_hook_never_raises(recorder):
    from crew_studio.memory_hooks import write_refinement_correction_memory

    db = _FakeDB(raises=["get_refinement_history"])
    assert write_refinement_correction_memory("job-1", config=_Cfg(), job={}, job_db=db) == 0


# ── Recall ordering ──────────────────────────────────────────────────────────


def test_recall_puts_corrections_first():
    # The block is truncated to a char budget. Corrections are the most
    # actionable recall there is, so they must not be the lines that get cut.
    from llamaindex_crew.memory.recall import _render_block

    episodes = [
        {"content": "an outcome", "metadata": {"type": "job_outcome"}},
        {"content": "a doc", "metadata": {"type": "reference_doc"}},
        {"content": "reviewer said no mongo", "metadata": {"type": "correction"}},
    ]
    block = _render_block(episodes, max_chars=4000)
    lines = [ln for ln in block.splitlines() if ln.startswith("- ")]

    assert "no mongo" in lines[0]


def test_recall_block_keeps_relative_order_within_a_type():
    from llamaindex_crew.memory.recall import _render_block

    episodes = [
        {"content": "first correction", "metadata": {"type": "correction"}},
        {"content": "second correction", "metadata": {"type": "correction"}},
    ]
    lines = [ln for ln in _render_block(episodes, max_chars=4000).splitlines() if ln.startswith("- ")]

    assert "first" in lines[0] and "second" in lines[1]


# ── Idempotency across resume / retry ────────────────────────────────────────
#
# _run_job_async_impl runs again on resume and retry_failed, and each terminal
# completion re-enters the post-job hook. Without a write-time guard the same
# plan feedback is stored two or three times for one job, and recall fills with
# duplicates of a single reviewer comment.


class _MetaDB(_FakeDB):
    """FakeDB that also round-trips job metadata, like JobDatabase does."""

    def __init__(self, job, **kw):
        super().__init__(**kw)
        self._job = job

    def get_job(self, job_id):
        return self._job

    def update_job(self, job_id, fields):
        if "metadata" in fields:
            self._job["metadata"] = json.loads(fields["metadata"])


def test_second_run_does_not_rewrite_the_same_corrections(recorder):
    job = {"team_id": "acme", "metadata": {
        "framework": "frappe", "plan_feedback_history": [{"feedback": "no mongo"}],
    }}
    db = _MetaDB(job)

    first = _hook()("job-1", config=_Cfg(), job=job, job_db=db)
    second = _hook()("job-1", config=_Cfg(), job=db.get_job("job-1"), job_db=db)

    assert first == 1
    assert second == 0, "a retried job must not duplicate its corrections"
    assert len(recorder.writes) == 1


def test_new_corrections_on_a_retry_are_still_written(recorder):
    job = {"team_id": "acme", "metadata": {
        "framework": "frappe", "plan_feedback_history": [{"feedback": "no mongo"}],
    }}
    db = _MetaDB(job)
    _hook()("job-1", config=_Cfg(), job=job, job_db=db)

    # The retry produced a second round of review feedback.
    db.get_job("job-1")["metadata"]["plan_feedback_history"].append({"feedback": "also no redis"})
    written = _hook()("job-1", config=_Cfg(), job=db.get_job("job-1"), job_db=db)

    assert written == 1
    assert "also no redis" in recorder.writes[-1]["content"]


def test_dedup_state_is_persisted_to_the_job_row(recorder):
    job = {"team_id": "acme", "metadata": {
        "framework": "frappe", "plan_feedback_history": [{"feedback": "no mongo"}],
    }}
    db = _MetaDB(job)
    _hook()("job-1", config=_Cfg(), job=job, job_db=db)

    assert db.get_job("job-1")["metadata"].get("memory_correction_keys")


def test_without_a_db_dedup_degrades_to_writing(recorder):
    # No job_db means no place to persist the guard. Writing a possible
    # duplicate beats dropping a real correction.
    job = {"metadata": {"plan_feedback_history": [{"feedback": "no mongo"}]}}
    assert _hook()("job-1", config=_Cfg(), job=job) == 1
    assert _hook()("job-1", config=_Cfg(), job=job) == 1
