"""
TDD tests for dashboard table sort and filter.

Tests cover:
  1. JobDatabase: get_jobs_paginated with sort_by / sort_order
  2. JobDatabase: get_jobs_count / get_jobs_paginated with status filter
  3. GET /api/jobs?sort_by=...&sort_order=...&status=...
"""
import json
import shutil
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
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test_jobs.db"
        yield JobDatabase(db_path), tmp


@pytest.fixture
def db_varied(db):
    """Database with jobs in varied statuses and visions."""
    database, tmp = db
    jobs = [
        ("j1", "Alpha project", "completed"),
        ("j2", "Beta calculator", "failed"),
        ("j3", "Charlie dashboard", "running"),
        ("j4", "Delta REST API", "queued"),
        ("j5", "[MTA] Echo migration", "completed"),
        ("j6", "[Refactor] Foxtrot rewrite", "failed"),
        ("j7", "Golf todo app", "running"),
    ]
    for job_id, vision, status in jobs:
        database.create_job(job_id, vision, f"{tmp}/ws-{job_id}")
        if status != "queued":
            database.update_job(job_id, {"status": status})
    return database


@pytest.fixture
def api_client():
    """Flask test client with a fresh temp DB."""
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


def _seed_api(client):
    """Seed API with jobs of various statuses."""
    visions = [
        "Alpha project",
        "Beta calculator",
        "Charlie dashboard",
        "[MTA] Delta migration",
        "[Refactor] Echo rewrite",
    ]
    ids = []
    for v in visions:
        resp = client.post("/api/jobs", json={"vision": v})
        ids.append(json.loads(resp.data)["job_id"])
    return ids


# ═══════════════════════════════════════════════════════════════════════════════
# 1. DB sort tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestSortPaginated:
    """get_jobs_paginated supports sort_by and sort_order."""

    def test_default_sort_newest_first(self, db_varied):
        page = db_varied.get_jobs_paginated(limit=10, offset=0)
        dates = [j["created_at"] for j in page]
        assert dates == sorted(dates, reverse=True)

    def test_sort_by_created_at_asc(self, db_varied):
        page = db_varied.get_jobs_paginated(
            limit=10, offset=0, sort_by="created_at", sort_order="asc"
        )
        dates = [j["created_at"] for j in page]
        assert dates == sorted(dates)

    def test_sort_by_vision_asc(self, db_varied):
        page = db_varied.get_jobs_paginated(
            limit=10, offset=0, sort_by="vision", sort_order="asc"
        )
        visions = [j["vision"] for j in page]
        assert visions == sorted(visions, key=str.lower)

    def test_sort_by_vision_desc(self, db_varied):
        page = db_varied.get_jobs_paginated(
            limit=10, offset=0, sort_by="vision", sort_order="desc"
        )
        visions = [j["vision"] for j in page]
        assert visions == sorted(visions, key=str.lower, reverse=True)

    def test_sort_by_status(self, db_varied):
        page = db_varied.get_jobs_paginated(
            limit=10, offset=0, sort_by="status", sort_order="asc"
        )
        statuses = [j["status"] for j in page]
        assert statuses == sorted(statuses)

    def test_sort_by_progress(self, db_varied):
        page = db_varied.get_jobs_paginated(
            limit=10, offset=0, sort_by="progress", sort_order="desc"
        )
        progresses = [j["progress"] for j in page]
        assert progresses == sorted(progresses, reverse=True)

    def test_invalid_sort_by_falls_back_to_created_at(self, db_varied):
        page = db_varied.get_jobs_paginated(
            limit=10, offset=0, sort_by="INVALID_COLUMN"
        )
        dates = [j["created_at"] for j in page]
        assert dates == sorted(dates, reverse=True)

    def test_sort_combined_with_vision_filter(self, db_varied):
        page = db_varied.get_jobs_paginated(
            limit=10, offset=0,
            vision_filter="MTA",
            sort_by="vision", sort_order="asc",
        )
        assert len(page) == 1
        assert "MTA" in page[0]["vision"]

    def test_sort_combined_with_pagination(self, db_varied):
        page1 = db_varied.get_jobs_paginated(
            limit=3, offset=0, sort_by="vision", sort_order="asc"
        )
        page2 = db_varied.get_jobs_paginated(
            limit=3, offset=3, sort_by="vision", sort_order="asc"
        )
        all_visions = [j["vision"] for j in page1 + page2]
        assert all_visions == sorted(all_visions, key=str.lower)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. DB status filter tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestStatusFilter:
    """get_jobs_count and get_jobs_paginated support status_filter."""

    def test_count_by_status(self, db_varied):
        assert db_varied.get_jobs_count(status_filter="completed") == 2
        assert db_varied.get_jobs_count(status_filter="failed") == 2
        assert db_varied.get_jobs_count(status_filter="running") == 2
        assert db_varied.get_jobs_count(status_filter="queued") == 1

    def test_paginated_by_status(self, db_varied):
        page = db_varied.get_jobs_paginated(
            limit=10, offset=0, status_filter="running"
        )
        assert len(page) == 2
        assert all(j["status"] == "running" for j in page)

    def test_status_no_matches(self, db_varied):
        assert db_varied.get_jobs_count(status_filter="cancelled") == 0
        page = db_varied.get_jobs_paginated(
            limit=10, offset=0, status_filter="cancelled"
        )
        assert page == []

    def test_status_none_returns_all(self, db_varied):
        assert db_varied.get_jobs_count(status_filter=None) == 7

    def test_status_combined_with_vision_filter(self, db_varied):
        count = db_varied.get_jobs_count(
            vision_filter="MTA", status_filter="completed"
        )
        assert count == 1

    def test_paginated_status_combined_with_vision(self, db_varied):
        page = db_varied.get_jobs_paginated(
            limit=10, offset=0,
            vision_filter="Refactor", status_filter="failed",
        )
        assert len(page) == 1
        assert "[Refactor]" in page[0]["vision"]
        assert page[0]["status"] == "failed"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. API endpoint sort/filter tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestAPISortFilter:
    """GET /api/jobs with sort and filter query params."""

    def test_sort_by_vision_asc(self, api_client):
        client, _ = api_client
        _seed_api(client)
        resp = client.get("/api/jobs?sort_by=vision&sort_order=asc&page_size=100")
        data = json.loads(resp.data)
        visions = [j["vision"] for j in data["jobs"]]
        assert visions == sorted(visions, key=str.lower)

    def test_sort_by_created_at_asc(self, api_client):
        client, _ = api_client
        _seed_api(client)
        resp = client.get("/api/jobs?sort_by=created_at&sort_order=asc&page_size=100")
        data = json.loads(resp.data)
        dates = [j["created_at"] for j in data["jobs"]]
        assert dates == sorted(dates)

    def test_default_sort_desc(self, api_client):
        client, _ = api_client
        _seed_api(client)
        resp = client.get("/api/jobs?page_size=100")
        data = json.loads(resp.data)
        dates = [j["created_at"] for j in data["jobs"]]
        assert dates == sorted(dates, reverse=True)

    def test_status_filter(self, api_client):
        client, db = api_client
        ids = _seed_api(client)
        db.update_job(ids[0], {"status": "completed"})
        db.update_job(ids[1], {"status": "completed"})
        resp = client.get("/api/jobs?status=completed&page_size=100")
        data = json.loads(resp.data)
        assert all(j["status"] == "completed" for j in data["jobs"])
        assert data["total"] == 2

    def test_status_and_vision_combined(self, api_client):
        client, db = api_client
        ids = _seed_api(client)
        db.update_job(ids[3], {"status": "completed"})
        resp = client.get(
            "/api/jobs?vision_contains=MTA&status=completed&page_size=100"
        )
        data = json.loads(resp.data)
        assert data["total"] == 1
        assert "MTA" in data["jobs"][0]["vision"]

    def test_invalid_sort_by_ignored(self, api_client):
        client, _ = api_client
        _seed_api(client)
        resp = client.get("/api/jobs?sort_by=BADCOL&page_size=100")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data["jobs"]) >= 5

    def test_sort_order_invalid_defaults_desc(self, api_client):
        client, _ = api_client
        _seed_api(client)
        resp = client.get("/api/jobs?sort_order=BADVAL&page_size=100")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        dates = [j["created_at"] for j in data["jobs"]]
        assert dates == sorted(dates, reverse=True)
