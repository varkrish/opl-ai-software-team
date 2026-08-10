"""
The scope a job reads from must be the scope the write path records under.

It was not. Both seeders resolved their scope from ``self._job_data`` — an
attribute nothing in the codebase ever assigns — so ``resolve_scope`` received
``{}`` and fell back to org ``default``. Meanwhile ``write_approved_solution_memory``
resolved from the real job row and recorded under ``owner_id``.

Live proof on job 9a0c6125: the workflow resolved ``org=default project=fastapi
domain=general`` while job e4abf072 sat in the database under
``org=mock-user-123 project=fastapi domain=general``. Both seeders returned None
for the scope the workflow actually used, and returned the stored blueprint for
the scope the writer used. No live job could ever recall another, including via
the wiring-contract seeder, which had looked correctly wired since it was
written.

Unit tests passed throughout, because every one of them constructed the scope by
hand and handed it straight to a seeder. Nothing exercised the step where the
workflow decides which scope to ask for.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from llamaindex_crew.memory.scope import resolve_scope


class _JobDb:
    def __init__(self, row):
        self._row = row

    def get_job(self, job_id):
        return self._row


def _workflow(job_db, workspace, job_id="job-1"):
    """A stand-in carrying only what _memory_scope touches."""
    from llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow

    wf = object.__new__(SoftwareDevWorkflow)
    wf.project_id = job_id
    wf.workspace_path = workspace
    wf.job_db = job_db
    return wf


def test_the_read_scope_matches_the_scope_the_writer_records_under(tmp_path):
    row = {"owner_id": "mock-user-123", "vision": "Build a FastAPI service"}

    written = resolve_scope(row, workspace_path=tmp_path)      # memory_hooks
    read = _workflow(_JobDb(row), tmp_path)._memory_scope()     # the workflow

    assert read.org_id == written.org_id == "mock-user-123"
    assert (read.org_id, read.project_id, read.domain) == (
        written.org_id, written.project_id, written.domain
    ), "reads and writes must address one scope or nothing is ever recalled"


def test_the_owner_is_not_silently_dropped(tmp_path):
    """The exact regression: org 'default' means every recorded job is invisible."""
    row = {"owner_id": "mock-user-123", "vision": "Build a FastAPI service"}

    scope = _workflow(_JobDb(row), tmp_path)._memory_scope()

    assert scope.org_id != "default", (
        "resolving from an unset attribute fell back to 'default', which no writer uses"
    )


def test_a_team_shares_one_pool(tmp_path):
    row = {"team_id": "acme", "owner_id": "mock-user-123", "vision": "Build a service"}

    assert _workflow(_JobDb(row), tmp_path)._memory_scope().org_id == "acme"


def test_the_scope_is_resolved_once(tmp_path):
    """Both seeders ask for it; the job row should not be re-fetched each time."""
    class _Counting(_JobDb):
        calls = 0

        def get_job(self, job_id):
            _Counting.calls += 1
            return self._row

    wf = _workflow(_Counting({"owner_id": "u", "vision": "v"}), tmp_path)
    first, second = wf._memory_scope(), wf._memory_scope()

    assert first is second
    assert _Counting.calls == 1


def test_a_missing_job_row_does_not_raise(tmp_path):
    """Recall is never worth failing a build over."""
    assert _workflow(_JobDb(None), tmp_path)._memory_scope().org_id == "default"
    assert _workflow(None, tmp_path)._memory_scope().org_id == "default"


def test_an_unreadable_job_db_does_not_raise(tmp_path):
    class _Broken:
        def get_job(self, job_id):
            raise RuntimeError("database is locked")

    assert _workflow(_Broken(), tmp_path)._memory_scope().org_id == "default"
