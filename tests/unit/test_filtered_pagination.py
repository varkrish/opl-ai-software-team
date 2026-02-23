"""
TDD tests for filtered pagination on Refactor and Migration pages.

Tests cover:
  1. JobDatabase.get_jobs_count(vision_filter=...) and get_jobs_paginated(vision_filter=...)
  2. GET /api/jobs?vision_contains=... server-side filtering with pagination
  3. Edge cases: no matches, combined filters, case-insensitive matching
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
def db_mixed_jobs(db):
    """Database seeded with a realistic mix of job types."""
    database, tmp = db
    jobs = [
        ("job-r1", "[Refactor] legacy-app - Modernize to Spring Boot"),
        ("job-r2", "[Refactor] old-service.zip - Convert to Quarkus"),
        ("job-r3", "[Refactor] monolith - Microservices split"),
        ("job-m1", "[MTA] legacy-inventory-system.zip"),
        ("job-m2", "[MTA Migration] eap6-to-eap8"),
        ("job-m3", "[MTA] payment-service migration"),
        ("job-b1", "Build a REST API for a task management system"),
        ("job-b2", "Build a simple JS calculator"),
        ("job-b3", "Create a weather dashboard app"),
        ("job-b4", "Build a todo app with React"),
    ]
    for job_id, vision in jobs:
        database.create_job(job_id, vision, f"{tmp}/ws-{job_id}")
    return database


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Database-level filtered count tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetJobsCountFiltered:
    """get_jobs_count with vision_filter returns correct filtered counts."""

    def test_no_filter_returns_all(self, db_mixed_jobs):
        assert db_mixed_jobs.get_jobs_count() == 10

    def test_filter_refactor(self, db_mixed_jobs):
        count = db_mixed_jobs.get_jobs_count(vision_filter="Refactor")
        assert count == 3

    def test_filter_mta(self, db_mixed_jobs):
        count = db_mixed_jobs.get_jobs_count(vision_filter="MTA")
        assert count == 3

    def test_filter_no_matches(self, db_mixed_jobs):
        count = db_mixed_jobs.get_jobs_count(vision_filter="NONEXISTENT_KEYWORD")
        assert count == 0

    def test_filter_case_insensitive(self, db_mixed_jobs):
        count = db_mixed_jobs.get_jobs_count(vision_filter="refactor")
        assert count == 3

    def test_filter_partial_match(self, db_mixed_jobs):
        count = db_mixed_jobs.get_jobs_count(vision_filter="Build")
        assert count == 3

    def test_empty_filter_returns_all(self, db_mixed_jobs):
        count = db_mixed_jobs.get_jobs_count(vision_filter="")
        assert count == 10

    def test_none_filter_returns_all(self, db_mixed_jobs):
        count = db_mixed_jobs.get_jobs_count(vision_filter=None)
        assert count == 10

    def test_empty_db(self, db):
        database, _ = db
        assert database.get_jobs_count(vision_filter="Refactor") == 0


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Database-level filtered paginated query tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetJobsPaginatedFiltered:
    """get_jobs_paginated with vision_filter returns correct filtered slices."""

    def test_filter_refactor_first_page(self, db_mixed_jobs):
        page = db_mixed_jobs.get_jobs_paginated(
            limit=2, offset=0, vision_filter="Refactor"
        )
        assert len(page) == 2
        assert all("[Refactor]" in j["vision"] for j in page)

    def test_filter_refactor_second_page(self, db_mixed_jobs):
        page = db_mixed_jobs.get_jobs_paginated(
            limit=2, offset=2, vision_filter="Refactor"
        )
        assert len(page) == 1
        assert "[Refactor]" in page[0]["vision"]

    def test_filter_mta_all(self, db_mixed_jobs):
        page = db_mixed_jobs.get_jobs_paginated(
            limit=100, offset=0, vision_filter="MTA"
        )
        assert len(page) == 3
        assert all("MTA" in j["vision"] for j in page)

    def test_filter_no_matches(self, db_mixed_jobs):
        page = db_mixed_jobs.get_jobs_paginated(
            limit=10, offset=0, vision_filter="NONEXISTENT"
        )
        assert page == []

    def test_filter_case_insensitive(self, db_mixed_jobs):
        page = db_mixed_jobs.get_jobs_paginated(
            limit=10, offset=0, vision_filter="mta"
        )
        assert len(page) == 3

    def test_no_filter_returns_all(self, db_mixed_jobs):
        page = db_mixed_jobs.get_jobs_paginated(limit=100, offset=0)
        assert len(page) == 10

    def test_filter_with_offset_beyond_total(self, db_mixed_jobs):
        page = db_mixed_jobs.get_jobs_paginated(
            limit=10, offset=100, vision_filter="Refactor"
        )
        assert page == []

    def test_filtered_results_ordered_newest_first(self, db_mixed_jobs):
        page = db_mixed_jobs.get_jobs_paginated(
            limit=10, offset=0, vision_filter="Refactor"
        )
        dates = [j["created_at"] for j in page]
        assert dates == sorted(dates, reverse=True)

    def test_empty_db(self, db):
        database, _ = db
        page = database.get_jobs_paginated(
            limit=10, offset=0, vision_filter="Refactor"
        )
        assert page == []

    def test_consistency_count_and_paginated(self, db_mixed_jobs):
        """Filtered count should equal total length of all paginated results."""
        count = db_mixed_jobs.get_jobs_count(vision_filter="Refactor")
        all_filtered = db_mixed_jobs.get_jobs_paginated(
            limit=100, offset=0, vision_filter="Refactor"
        )
        assert count == len(all_filtered)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. API endpoint tests for filtered pagination
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def api_client():
    """Flask test client with a fresh temp DB (isolated from production data)."""
    import shutil
    import crew_studio.llamaindex_web_app as webapp

    tmp = tempfile.mkdtemp()
    fresh_db = JobDatabase(Path(tmp) / "test_api.db")
    original_db = webapp.job_db
    webapp.job_db = fresh_db
    webapp.app.config["TESTING"] = True
    webapp.app.config["JOB_DB"] = fresh_db
    with webapp.app.test_client() as client:
        yield client, fresh_db
    webapp.job_db = original_db
    shutil.rmtree(tmp, ignore_errors=True)


def _create_typed_jobs(client):
    """Create a mix of refactor, migration, and regular jobs."""
    visions = [
        "[Refactor] legacy-app - Modernize to Spring Boot",
        "[Refactor] old-service.zip - Convert to Quarkus",
        "[Refactor] monolith - Microservices split",
        "[MTA] legacy-inventory-system.zip",
        "[MTA Migration] eap6-to-eap8",
        "[MTA] payment-service migration",
        "Build a REST API",
        "Build a calculator",
        "Create a dashboard",
    ]
    ids = []
    for v in visions:
        resp = client.post("/api/jobs", json={"vision": v})
        ids.append(json.loads(resp.data)["job_id"])
    return ids


class TestFilteredPaginationAPI:
    """GET /api/jobs?vision_contains=... returns filtered paginated results."""

    def test_filter_refactor_returns_only_refactor(self, api_client):
        client, _ = api_client
        _create_typed_jobs(client)
        resp = client.get("/api/jobs?vision_contains=Refactor&page_size=100")
        data = json.loads(resp.data)
        assert data["total"] == 3
        assert len(data["jobs"]) == 3
        assert all("Refactor" in j["vision"] for j in data["jobs"])

    def test_filter_mta_returns_only_mta(self, api_client):
        client, _ = api_client
        _create_typed_jobs(client)
        resp = client.get("/api/jobs?vision_contains=MTA&page_size=100")
        data = json.loads(resp.data)
        assert data["total"] == 3
        assert len(data["jobs"]) == 3
        assert all("MTA" in j["vision"] for j in data["jobs"])

    def test_filter_with_pagination(self, api_client):
        client, _ = api_client
        _create_typed_jobs(client)
        resp = client.get("/api/jobs?vision_contains=Refactor&page=1&page_size=2")
        data = json.loads(resp.data)
        assert data["total"] == 3
        assert data["page"] == 1
        assert data["page_size"] == 2
        assert len(data["jobs"]) == 2

    def test_filter_second_page(self, api_client):
        client, _ = api_client
        _create_typed_jobs(client)
        resp = client.get("/api/jobs?vision_contains=Refactor&page=2&page_size=2")
        data = json.loads(resp.data)
        assert data["total"] == 3
        assert data["page"] == 2
        assert len(data["jobs"]) == 1

    def test_filter_no_matches(self, api_client):
        client, _ = api_client
        _create_typed_jobs(client)
        resp = client.get("/api/jobs?vision_contains=NONEXISTENT")
        data = json.loads(resp.data)
        assert data["total"] == 0
        assert data["jobs"] == []

    def test_no_filter_returns_all(self, api_client):
        client, _ = api_client
        _create_typed_jobs(client)
        resp = client.get("/api/jobs?page_size=100")
        data = json.loads(resp.data)
        assert data["total"] >= 9

    def test_filter_case_insensitive(self, api_client):
        client, _ = api_client
        _create_typed_jobs(client)
        resp = client.get("/api/jobs?vision_contains=refactor&page_size=100")
        data = json.loads(resp.data)
        assert data["total"] == 3

    def test_response_shape_with_filter(self, api_client):
        client, _ = api_client
        _create_typed_jobs(client)
        resp = client.get("/api/jobs?vision_contains=MTA")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "jobs" in data
        assert "total" in data
        assert "page" in data
        assert "page_size" in data

    def test_filter_all_pages_cover_all_filtered_jobs(self, api_client):
        """Iterating all pages with a filter should yield every matching job."""
        client, _ = api_client
        _create_typed_jobs(client)

        seen_ids: set = set()
        page = 1
        while True:
            resp = client.get(
                f"/api/jobs?vision_contains=Refactor&page={page}&page_size=1"
            )
            data = json.loads(resp.data)
            if not data["jobs"]:
                break
            for job in data["jobs"]:
                seen_ids.add(job["id"])
            page += 1

        assert len(seen_ids) == 3

    def test_filter_build_keyword(self, api_client):
        client, _ = api_client
        _create_typed_jobs(client)
        resp = client.get("/api/jobs?vision_contains=Build&page_size=100")
        data = json.loads(resp.data)
        assert data["total"] == 2
        assert all("Build" in j["vision"] for j in data["jobs"])
