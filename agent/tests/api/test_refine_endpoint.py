"""
API tests for POST /api/jobs/<job_id>/refine and GET /api/jobs/<job_id>/refinements
"""
import json
import sys
import uuid
import tempfile
from pathlib import Path
from unittest.mock import patch

# Repo root for crew_studio import
root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "agent"))
sys.path.insert(0, str(root / "agent" / "src"))

from crew_studio.llamaindex_web_app import app, job_db

app.config["TESTING"] = True


def _create_completed_job():
    """Helper: create a job directly in the DB (no background threads)."""
    job_id = str(uuid.uuid4())
    ws = tempfile.mkdtemp()
    job_db.create_job(job_id, "Test project", ws)
    job_db.update_job(job_id, {"status": "completed", "current_phase": "completed"})
    return job_id


def test_refine_job_not_found():
    """404 for missing job_id"""
    with app.test_client() as client:
        response = client.post(
            "/api/jobs/00000000-0000-0000-0000-000000000000/refine",
            json={"prompt": "Add a comment"},
            content_type="application/json",
        )
        assert response.status_code == 404


def test_refine_job_missing_prompt():
    """400 when prompt is missing or empty"""
    job_id = _create_completed_job()
    with app.test_client() as client:
        response = client.post(
            f"/api/jobs/{job_id}/refine",
            json={},
            content_type="application/json",
        )
        assert response.status_code == 400
        response2 = client.post(
            f"/api/jobs/{job_id}/refine",
            json={"prompt": "   "},
            content_type="application/json",
        )
        assert response2.status_code == 400


def test_refine_job_invalid_file_path():
    """400 when file_path contains .."""
    job_id = _create_completed_job()
    with app.test_client() as client:
        response = client.post(
            f"/api/jobs/{job_id}/refine",
            json={"prompt": "Fix bug", "file_path": "../../etc/passwd"},
            content_type="application/json",
        )
        assert response.status_code == 400


def test_refine_returns_202_and_refinement_record():
    """Valid refine request (project-wide, no file_path) returns 202"""
    job_id = _create_completed_job()
    with app.test_client() as client:
        # Mock Thread so background refine doesn't interfere with DB
        with patch("crew_studio.llamaindex_web_app.threading.Thread"):
            response = client.post(
                f"/api/jobs/{job_id}/refine",
                json={"prompt": "Add a TODO comment at the top"},
                content_type="application/json",
            )
        assert response.status_code == 202
        data = json.loads(response.data)
        assert data.get("status") == "refining"
        assert "refinement_id" in data
        history = job_db.get_refinement_history(job_id)
        assert len(history) >= 1
        assert history[0]["prompt"] == "Add a TODO comment at the top"
        assert history[0]["status"] == "running"


def test_refine_with_valid_file_path_returns_202():
    """Valid refine with a file_path (file-level scope) returns 202, not 400.
    Regression: _is_safe_relative_path used to reject all valid paths."""
    job_id = _create_completed_job()
    with app.test_client() as client:
        # Mock Thread so background refine doesn't run (we only test API validation)
        with patch("crew_studio.llamaindex_web_app.threading.Thread"):
            response = client.post(
                f"/api/jobs/{job_id}/refine",
                json={"prompt": "Add comments", "file_path": "calculator-app/src/components/Display.js"},
                content_type="application/json",
            )
        assert response.status_code == 202, (
            f"Expected 202 for valid file_path, got {response.status_code}: "
            f"{response.data.decode()}"
        )


def test_is_safe_relative_path_accepts_valid_paths():
    """_is_safe_relative_path must accept real relative file paths (regression test)."""
    from crew_studio.llamaindex_web_app import _is_safe_relative_path

    # Valid paths that users would actually send
    assert _is_safe_relative_path("src/app.js") is True
    assert _is_safe_relative_path("calculator-app/src/components/Display.js") is True
    assert _is_safe_relative_path("index.html") is True
    assert _is_safe_relative_path("tests/unit/test_app.py") is True
    assert _is_safe_relative_path("my-project/src/main.ts") is True

    # Invalid paths that should be rejected
    assert _is_safe_relative_path("../../etc/passwd") is False
    assert _is_safe_relative_path("/etc/passwd") is False
    assert _is_safe_relative_path("../secret") is False
    assert _is_safe_relative_path("") is False


def test_refine_concurrent_409():
    """409 when refinement already in progress"""
    job_id = _create_completed_job()
    job_db.update_job(job_id, {"current_phase": "refining"})
    with app.test_client() as client:
        response = client.post(
            f"/api/jobs/{job_id}/refine",
            json={"prompt": "Another change"},
            content_type="application/json",
        )
        assert response.status_code == 409
        data = json.loads(response.data)
        assert "already in progress" in data.get("error", "").lower()


def test_get_refinements_404():
    """404 for refinements of non-existent job"""
    with app.test_client() as client:
        response = client.get("/api/jobs/00000000-0000-0000-0000-000000000000/refinements")
        assert response.status_code == 404


def test_get_refinements_empty():
    """200 and empty list when no refinements"""
    job_id = _create_completed_job()
    with app.test_client() as client:
        response = client.get(f"/api/jobs/{job_id}/refinements")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["refinements"] == []


def test_preview_404_job():
    """404 for preview when job not found"""
    with app.test_client() as client:
        response = client.get("/api/jobs/00000000-0000-0000-0000-000000000000/preview/index.html")
        assert response.status_code == 404


def test_preview_invalid_path():
    """400 for preview with path escape"""
    job_id = _create_completed_job()
    with app.test_client() as client:
        response = client.get(f"/api/jobs/{job_id}/preview/../../../etc/passwd")
        assert response.status_code == 400
