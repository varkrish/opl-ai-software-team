"""
API tests for GET /api/jobs/<job_id>/download (workspace ZIP download).
"""
import json
import sys
import uuid
import zipfile
import tempfile
import shutil
from pathlib import Path
from io import BytesIO
from unittest.mock import patch

root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "agent"))
sys.path.insert(0, str(root / "agent" / "src"))

from crew_studio.llamaindex_web_app import app, job_db, base_workspace_path

app.config["TESTING"] = True


def _create_job_with_workspace_files():
    """Create a job with a real temp workspace containing at least one file.
    Returns (job_id, workspace_path).
    """
    job_id = str(uuid.uuid4())
    ws = tempfile.mkdtemp()
    job_db.create_job(job_id, "Download test project", ws)
    job_db.update_job(job_id, {"status": "completed", "current_phase": "completed"})
    (Path(ws) / "README.md").write_text("# Test Project", encoding="utf-8")
    (Path(ws) / "src").mkdir(exist_ok=True)
    (Path(ws) / "src" / "app.js").write_text("console.log('hello');", encoding="utf-8")
    return job_id, ws


def test_download_job_not_found():
    """404 when job_id does not exist."""
    with app.test_client() as client:
        response = client.get("/api/jobs/00000000-0000-0000-0000-000000000000/download")
    assert response.status_code == 404
    data = json.loads(response.data)
    assert "error" in data
    assert "not found" in data["error"].lower()


def test_download_workspace_not_found():
    """404 when job exists but workspace directory does not exist."""
    job_id = str(uuid.uuid4())
    job_db.create_job(job_id, "Orphan job", "/nonexistent/workspace/path")
    job_db.update_job(job_id, {"status": "completed"})
    with app.test_client() as client:
        response = client.get(f"/api/jobs/{job_id}/download")
    assert response.status_code == 404
    data = json.loads(response.data)
    assert "error" in data
    assert "workspace" in data["error"].lower()


def test_download_returns_200_and_zip():
    """200 with application/zip and valid ZIP content when job and workspace exist."""
    job_id, ws = _create_job_with_workspace_files()
    try:
        with app.test_client() as client:
            response = client.get(f"/api/jobs/{job_id}/download")
        assert response.status_code == 200, response.data.decode()
        assert response.content_type and "application/zip" in response.content_type
        buf = BytesIO(response.data)
        with zipfile.ZipFile(buf, "r") as zf:
            names = zf.namelist()
            assert "README.md" in names
            assert "src/app.js" in names or "src\\app.js" in names
            content = zf.read("README.md").decode("utf-8")
            assert "Test Project" in content
    finally:
        if Path(ws).exists():
            shutil.rmtree(ws, ignore_errors=True)


def test_download_resolve_canonical_workspace():
    """Download succeeds using canonical base_workspace_path/job-{id} when stored path is missing.
    Regression: stored relative path or deleted temp dir; resolver falls back to canonical."""
    job_id = str(uuid.uuid4())
    tmp_base = Path(tempfile.mkdtemp())
    try:
        canonical = tmp_base / f"job-{job_id}"
        canonical.mkdir(parents=True, exist_ok=True)
        (canonical / "hello.txt").write_text("world", encoding="utf-8")
        job_db.create_job(job_id, "Canonical test", "/nonexistent/stored/path")
        job_db.update_job(job_id, {"status": "completed"})
        with patch("crew_studio.llamaindex_web_app.base_workspace_path", tmp_base):
            with app.test_client() as client:
                response = client.get(f"/api/jobs/{job_id}/download")
        assert response.status_code == 200
        assert "application/zip" in (response.content_type or "")
        buf = BytesIO(response.data)
        with zipfile.ZipFile(buf, "r") as zf:
            assert "hello.txt" in zf.namelist()
            assert zf.read("hello.txt").decode("utf-8") == "world"
    finally:
        shutil.rmtree(tmp_base, ignore_errors=True)


def test_download_excludes_internal_agent_files():
    """Download ZIP must not include agent_prompts.json, crew_errors.log, state_*.json, tasks_*.db."""
    job_id, ws = _create_job_with_workspace_files()
    try:
        (Path(ws) / "agent_prompts.json").write_text("{}", encoding="utf-8")
        (Path(ws) / "crew_errors.log").write_text("log", encoding="utf-8")
        (Path(ws) / f"state_{job_id}.json").write_text("{}", encoding="utf-8")
        (Path(ws) / f"tasks_{job_id}.db").write_bytes(b"\x00")
        with app.test_client() as client:
            response = client.get(f"/api/jobs/{job_id}/download")
        assert response.status_code == 200
        buf = BytesIO(response.data)
        with zipfile.ZipFile(buf, "r") as zf:
            names = zf.namelist()
            assert "README.md" in names
            assert "agent_prompts.json" not in names
            assert "crew_errors.log" not in names
            assert not any("state_" in n and n.endswith(".json") for n in names)
            assert not any("tasks_" in n and n.endswith(".db") for n in names)
    finally:
        if Path(ws).exists():
            shutil.rmtree(ws, ignore_errors=True)


def test_download_content_disposition():
    """Response includes Content-Disposition attachment and filename."""
    job_id, ws = _create_job_with_workspace_files()
    try:
        with app.test_client() as client:
            response = client.get(f"/api/jobs/{job_id}/download")
        assert response.status_code == 200
        disp = response.headers.get("Content-Disposition")
        assert disp is not None
        assert "attachment" in disp
        assert "project-" in disp and ".zip" in disp
    finally:
        if Path(ws).exists():
            shutil.rmtree(ws, ignore_errors=True)
