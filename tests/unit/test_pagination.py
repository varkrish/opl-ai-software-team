"""
TDD tests for dashboard pagination.

Tests cover:
  1. JobDatabase.get_jobs_count() and get_jobs_paginated()
  2. GET /api/jobs pagination query params (page, page_size)
  3. Edge cases: empty DB, out-of-range page, bad params
"""
import json
import tempfile
from pathlib import Path

import pytest
import sys

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "agent"))
sys.path.insert(0, str(root / "agent" / "src"))

from crew_studio.job_database import JobDatabase


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def db():
    """Fresh temp database for each test."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test_jobs.db"
        yield JobDatabase(db_path), tmp


@pytest.fixture
def db_with_jobs(db):
    """Database seeded with 25 jobs (vision='Job 01' .. 'Job 25')."""
    database, tmp = db
    for i in range(1, 26):
        database.create_job(
            f"job-{i:03d}",
            f"Job {i:02d}",
            f"{tmp}/ws-{i}",
        )
    return database


# ═══════════════════════════════════════════════════════════════════════════════
# 1. JobDatabase unit tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetJobsCount:
    """get_jobs_count returns total number of jobs."""

    def test_empty_db(self, db):
        database, _ = db
        assert database.get_jobs_count() == 0

    def test_with_jobs(self, db_with_jobs):
        assert db_with_jobs.get_jobs_count() == 25

    def test_after_delete(self, db_with_jobs):
        db_with_jobs.delete_job("job-001")
        assert db_with_jobs.get_jobs_count() == 24


class TestGetJobsPaginated:
    """get_jobs_paginated returns correct slices ordered by created_at DESC."""

    def test_first_page(self, db_with_jobs):
        page = db_with_jobs.get_jobs_paginated(limit=10, offset=0)
        assert len(page) == 10
        # Newest first → Job 25 should be first
        assert page[0]["vision"] == "Job 25"

    def test_second_page(self, db_with_jobs):
        page = db_with_jobs.get_jobs_paginated(limit=10, offset=10)
        assert len(page) == 10
        assert page[0]["vision"] == "Job 15"

    def test_last_page_partial(self, db_with_jobs):
        page = db_with_jobs.get_jobs_paginated(limit=10, offset=20)
        assert len(page) == 5
        assert page[0]["vision"] == "Job 05"
        assert page[-1]["vision"] == "Job 01"

    def test_offset_beyond_total(self, db_with_jobs):
        page = db_with_jobs.get_jobs_paginated(limit=10, offset=100)
        assert page == []

    def test_limit_larger_than_total(self, db_with_jobs):
        page = db_with_jobs.get_jobs_paginated(limit=100, offset=0)
        assert len(page) == 25

    def test_returns_dicts_with_expected_keys(self, db_with_jobs):
        page = db_with_jobs.get_jobs_paginated(limit=1, offset=0)
        assert len(page) == 1
        job = page[0]
        for key in ("id", "vision", "status", "progress", "current_phase", "created_at"):
            assert key in job, f"Missing key: {key}"

    def test_empty_db(self, db):
        database, _ = db
        assert database.get_jobs_paginated(limit=10, offset=0) == []

    def test_consistent_with_get_all_jobs(self, db_with_jobs):
        """Paginated results should match slices of get_all_jobs."""
        all_jobs = db_with_jobs.get_all_jobs()
        page1 = db_with_jobs.get_jobs_paginated(limit=10, offset=0)
        page2 = db_with_jobs.get_jobs_paginated(limit=10, offset=10)
        page3 = db_with_jobs.get_jobs_paginated(limit=10, offset=20)

        paginated_ids = [j["id"] for j in page1 + page2 + page3]
        all_ids = [j["id"] for j in all_jobs]
        assert paginated_ids == all_ids


# ═══════════════════════════════════════════════════════════════════════════════
# 2. API endpoint tests
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def api_client():
    """Flask test client with a clean temp DB."""
    from crew_studio.llamaindex_web_app import app, job_db

    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client, job_db


def _create_jobs(client, count):
    """Helper: create N jobs via the API and return their IDs."""
    ids = []
    for i in range(count):
        resp = client.post("/api/jobs", json={"vision": f"Test job {i + 1}"})
        ids.append(json.loads(resp.data)["job_id"])
    return ids


class TestListJobsPagination:
    """GET /api/jobs returns paginated results."""

    def test_response_shape(self, api_client):
        client, _ = api_client
        _create_jobs(client, 3)
        resp = client.get("/api/jobs")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "jobs" in data
        assert "total" in data
        assert "page" in data
        assert "page_size" in data

    def test_default_page_size(self, api_client):
        client, _ = api_client
        _create_jobs(client, 15)
        resp = client.get("/api/jobs")
        data = json.loads(resp.data)
        assert data["page"] == 1
        assert data["page_size"] == 10
        assert len(data["jobs"]) == 10
        assert data["total"] >= 15

    def test_custom_page_and_size(self, api_client):
        client, _ = api_client
        _create_jobs(client, 15)
        resp = client.get("/api/jobs?page=2&page_size=5")
        data = json.loads(resp.data)
        assert data["page"] == 2
        assert data["page_size"] == 5
        assert len(data["jobs"]) == 5

    def test_page_beyond_total(self, api_client):
        client, _ = api_client
        _create_jobs(client, 3)
        resp = client.get("/api/jobs?page=999")
        data = json.loads(resp.data)
        assert data["page"] == 999
        assert data["jobs"] == []
        assert data["total"] >= 3

    def test_page_size_clamped_to_max(self, api_client):
        """page_size > 100 should be clamped to 100."""
        client, _ = api_client
        _create_jobs(client, 2)
        resp = client.get("/api/jobs?page_size=500")
        data = json.loads(resp.data)
        assert data["page_size"] == 100

    def test_page_size_minimum(self, api_client):
        """page_size < 1 should be clamped to 1."""
        client, _ = api_client
        _create_jobs(client, 5)
        resp = client.get("/api/jobs?page_size=0")
        data = json.loads(resp.data)
        assert data["page_size"] == 1
        assert len(data["jobs"]) == 1

    def test_negative_page_clamped(self, api_client):
        """page < 1 should be clamped to 1."""
        client, _ = api_client
        _create_jobs(client, 3)
        resp = client.get("/api/jobs?page=-5")
        data = json.loads(resp.data)
        assert data["page"] == 1
        assert len(data["jobs"]) >= 1

    def test_all_pages_cover_all_jobs(self, api_client):
        """Iterating all pages should yield every job exactly once."""
        client, _ = api_client
        created_ids = set(_create_jobs(client, 12))

        seen_ids: set[str] = set()
        page = 1
        while True:
            resp = client.get(f"/api/jobs?page={page}&page_size=5")
            data = json.loads(resp.data)
            if not data["jobs"]:
                break
            for job in data["jobs"]:
                seen_ids.add(job["id"])
            page += 1

        assert created_ids.issubset(seen_ids)

    def test_jobs_have_expected_fields(self, api_client):
        client, _ = api_client
        _create_jobs(client, 1)
        resp = client.get("/api/jobs")
        data = json.loads(resp.data)
        job = data["jobs"][0]
        for key in ("id", "vision", "status", "progress", "current_phase", "created_at"):
            assert key in job, f"Missing key: {key}"

    def test_vision_truncated_over_100_chars(self, api_client):
        client, _ = api_client
        long_vision = "A" * 200
        client.post("/api/jobs", json={"vision": long_vision})
        resp = client.get("/api/jobs?page_size=100")
        data = json.loads(resp.data)
        matching = [j for j in data["jobs"] if j["vision"].startswith("AAAA")]
        assert len(matching) >= 1
        assert len(matching[0]["vision"]) <= 103  # 100 chars + '...'
        assert matching[0]["vision"].endswith("...")
