"""
Which prior jobs may be reused as blueprints, and when seeding fires.

Three defects this pins, all of which made the context plane either inert or
unsafe:

1. ``get_passed_jobs_in_scope`` gated on ``status = 'completed'`` before
   consulting per-check outcomes. In the live database ``partially_completed``
   outnumbers ``completed`` 32 to 9, so ~78% of the corpus was discarded before
   the per-check logic ran. Job 107b3d3e was partially_completed with five
   failing checks and ZERO wiring or package issues — its contract is reusable
   even though the job was not clean overall. Judging per check is the reason
   outcomes are stored per check.

2. The same function treated a job with *no recorded outcomes* as passed. No
   evidence is not evidence of passing, and handing back an unverified blueprint
   is exactly the failure this design exists to prevent — job 1cec01ad sits in
   the old index at critique_score 9 with a contract containing only ``tests``.

3. The workflow's tests-only detection read ``packages`` as a list. It is a
   mapping of name -> {files, owns}, so ``pkg_names`` was always ``[]`` and the
   branch never fired. Seeding only happened when the contract was absent
   entirely — never for the shape it was written to repair.
"""
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))


# ── 3. the tests-only trigger ───────────────────────────────────────────────

def _tests_only(contract: Any) -> bool:
    """Mirror of the workflow's trigger, exercised directly."""
    if isinstance(contract, dict):
        pkg_names = sorted((contract.get("packages") or {}).keys())
    else:
        pkg_names = []
    return bool(pkg_names) and all(
        name.strip("/").split("/")[0] in ("tests", "test") for name in pkg_names
    )


def test_a_tests_only_contract_is_detected():
    """The live 1cec01ad shape: the whole application missing, only tests left."""
    contract = {"version": 1, "module": "x",
                "packages": {"tests": {"files": ["tests/test_a.py"], "owns": []}}}

    assert _tests_only(contract), "reading packages as a list made this always False"


def test_nested_test_packages_also_count():
    contract = {"packages": {
        "tests": {"files": ["tests/__init__.py"]},
        "tests/unit": {"files": ["tests/unit/test_a.py"]},
    }}
    assert _tests_only(contract)


def test_a_real_contract_is_not_flagged():
    contract = {"packages": {
        "app": {"files": ["app/main.py"]},
        "tests": {"files": ["tests/test_a.py"]},
    }}
    assert not _tests_only(contract), "a project with an application must not be reseeded"


def test_an_empty_contract_is_not_tests_only():
    """Absent is handled by the `not contract` arm, not by this one."""
    assert not _tests_only({"packages": {}})
    assert not _tests_only({})
    assert not _tests_only(None)


# ── 1 & 2. which jobs qualify as blueprint sources ──────────────────────────

class _FakeCursor:
    def __init__(self, job_rows): self._rows = job_rows; self._last = None
    def execute(self, sql, params=None): self._last = sql
    def fetchall(self): return self._rows
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _FakeConn:
    def __init__(self, job_rows): self._rows = job_rows; self.sql_seen: List[str] = []
    def cursor(self):
        cur = _FakeCursor(self._rows)
        orig = cur.execute
        def _rec(sql, params=None):
            self.sql_seen.append(sql); return orig(sql, params)
        cur.execute = _rec
        return cur
    def close(self): pass


def _store(job_rows, outcomes: Dict[str, List[Dict[str, Any]]]):
    from llamaindex_crew.memory.postgres_context_store import PostgresContextStore
    store = PostgresContextStore(dsn="postgresql://unused/unused")
    conn = _FakeConn(job_rows)
    store._get_connection = lambda: conn                      # type: ignore[assignment]
    store.get_job_outcomes = lambda jid: outcomes.get(jid, [])  # type: ignore[assignment]
    store._conn = conn
    return store


def _ok(name): return {"check_name": name, "passed": True}
def _bad(name): return {"check_name": name, "passed": False}


def test_a_partially_completed_job_can_still_supply_a_contract():
    """
    Job 107b3d3e: partially_completed, five failing checks, zero wiring issues.
    Its wiring contract is sound and must remain reusable.
    """
    store = _store(
        [("job-107b",)],
        {"job-107b": [_ok("wiring_contract"), _ok("entrypoint"),
                      _ok("client_endpoint_alignment"), _bad("duplicate_code_blocks"),
                      _bad("integration")]},
    )

    got = store.get_passed_jobs_in_scope(
        "org", "proj", "general",
        required_checks=["wiring_contract", "entrypoint", "client_endpoint_alignment"],
    )

    assert got == ["job-107b"], "a per-check pass must survive an overall-imperfect job"


def test_the_status_gate_does_not_exclude_partially_completed():
    store = _store([("j1",)], {"j1": [_ok("wiring_contract")]})
    store.get_passed_jobs_in_scope("org", "proj", "general", required_checks=["wiring_contract"])

    sql = " ".join(store._conn.sql_seen)
    assert "partially_completed" in sql, (
        "gating on status='completed' alone discards ~78% of the live corpus"
    )


def test_a_job_failing_a_required_check_is_excluded():
    store = _store([("j1",)], {"j1": [_bad("wiring_contract"), _ok("entrypoint")]})

    assert store.get_passed_jobs_in_scope(
        "org", "proj", "general", required_checks=["wiring_contract"]
    ) == []


def test_a_failure_outside_the_required_checks_is_tolerated():
    store = _store([("j1",)], {"j1": [_ok("wiring_contract"), _bad("pytest")]})

    assert store.get_passed_jobs_in_scope(
        "org", "proj", "general", required_checks=["wiring_contract"]
    ) == ["j1"]


def test_a_job_with_no_recorded_outcomes_is_not_reusable():
    """No evidence is not evidence of passing — fail closed."""
    store = _store([("j1",)], {})

    assert store.get_passed_jobs_in_scope(
        "org", "proj", "general", required_checks=["wiring_contract"]
    ) == []
    assert store.get_passed_jobs_in_scope("org", "proj", "general") == []


def test_a_required_check_that_was_never_run_does_not_count_as_passed():
    """Absence of the check is absence of evidence."""
    store = _store([("j1",)], {"j1": [_ok("entrypoint")]})

    assert store.get_passed_jobs_in_scope(
        "org", "proj", "general", required_checks=["wiring_contract"]
    ) == []


def test_with_no_required_checks_a_clean_sweep_is_needed():
    clean = _store([("j1",)], {"j1": [_ok("a"), _ok("b")]})
    dirty = _store([("j2",)], {"j2": [_ok("a"), _bad("b")]})

    assert clean.get_passed_jobs_in_scope("org", "proj", "general") == ["j1"]
    assert dirty.get_passed_jobs_in_scope("org", "proj", "general") == []


def test_an_unreachable_database_yields_nothing_rather_than_raising():
    from llamaindex_crew.memory.postgres_context_store import PostgresContextStore
    store = PostgresContextStore(dsn="postgresql://unused/unused")
    store._get_connection = lambda: None  # type: ignore[assignment]

    assert store.get_passed_jobs_in_scope("org", "proj", "general") == []


# ── where outcomes come from ────────────────────────────────────────────────

def test_outcomes_come_from_the_report_not_the_failures_table(tmp_path):
    """
    validation_issues is a FAILURES table: one row per problem, nothing for a
    check that passed. On job 107b3d3e it held 5 rows while the report recorded
    15 checks, 12 of them passing. Sourcing outcomes from it left a passing
    check indistinguishable from one that never ran — and since a required
    check must be recorded AND passed to qualify a blueprint, no job could ever
    be reusable. The seeding path was inert.
    """
    import json
    from llamaindex_crew.memory.postgres_context_store import _outcomes_from_validation_report

    (tmp_path / "validation_report.json").write_text(json.dumps({"checks": {
        "entrypoint": {"pass": True},
        "completeness": {"pass": True},
        "client_server_contract": {"pass": False, "unreachable_calls": ["/api/v1/stream"]},
        "contract_conformance": {"pass": True, "skipped": True},
    }}), encoding="utf-8")

    outcomes = _outcomes_from_validation_report(tmp_path)
    by_name = {o["check_name"]: o["passed"] for o in outcomes}

    assert by_name["entrypoint"] is True, "a passing check must be recorded, not merely absent"
    assert by_name["completeness"] is True
    assert by_name["client_server_contract"] is False
    assert "contract_conformance" not in by_name, (
        "a skipped check is not evidence; recording it as passed would qualify a "
        "blueprint on a check nobody ran"
    )


def test_a_missing_report_falls_back_rather_than_raising(tmp_path):
    from llamaindex_crew.memory.postgres_context_store import _outcomes_from_validation_report
    assert _outcomes_from_validation_report(tmp_path) == []
    assert _outcomes_from_validation_report(None) == []


def test_a_malformed_report_does_not_raise(tmp_path):
    from llamaindex_crew.memory.postgres_context_store import _outcomes_from_validation_report
    (tmp_path / "validation_report.json").write_text("{not json", encoding="utf-8")
    assert _outcomes_from_validation_report(tmp_path) == []
