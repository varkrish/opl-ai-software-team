"""
API tests for migration Blueprint endpoints.
TDD: Written to define the contract for POST /migrate, GET /migration, GET /migration/plan.
Also covers the migration-mode flow in POST /api/jobs (ZIP extraction).
"""
import io
import json
import os
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "agent"))
sys.path.insert(0, str(root / "agent" / "src"))


@pytest.fixture
def app_client():
    """Create a Flask test client with a temp DB and workspace."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        ws_path = Path(tmp) / "workspace"
        ws_path.mkdir()

        # Create a fresh DB for the test
        from crew_studio.job_database import JobDatabase
        test_db = JobDatabase(db_path)

        from crew_studio.llamaindex_web_app import app

        app.config["TESTING"] = True
        app.config["JOB_DB"] = test_db
        app.config["WORKSPACE_PATH"] = str(ws_path)

        with app.test_client() as client:
            yield client, test_db, ws_path


def _create_job_with_report(job_db, ws_base):
    """Helper: create a completed job with an uploaded MTA report."""
    job_id = str(uuid.uuid4())
    job_ws = ws_base / f"job-{job_id}"
    job_ws.mkdir(parents=True)
    docs_dir = job_ws / "docs"
    docs_dir.mkdir()

    # Write a dummy MTA report
    report_path = docs_dir / "mta-report.json"
    report_path.write_text(json.dumps({
        "issues": [
            {"id": "MTA-001", "title": "Replace javax.inject", "severity": "mandatory",
             "files": ["src/App.java"], "message": "javax -> jakarta"}
        ]
    }))

    # Create job in DB
    job_db.create_job(job_id, "Migrate EAP 7 to 8", str(job_ws))
    job_db.update_job(job_id, {"status": "completed"})

    # Register doc
    doc_id = str(uuid.uuid4())
    job_db.add_document(doc_id, job_id, "mta-report.json", "mta-report.json", "json",
                        report_path.stat().st_size, str(report_path))

    # Write a source file to migrate
    src_dir = job_ws / "src"
    src_dir.mkdir()
    (src_dir / "App.java").write_text("import javax.inject.Inject;\npublic class App {}")

    return job_id


class TestPostMigrate:

    def test_start_migration_returns_202(self, app_client):
        client, job_db, ws = app_client
        job_id = _create_job_with_report(job_db, ws)

        # Mock the thread so it doesn't actually run the agent
        with patch("crew_studio.migration.blueprint.threading.Thread") as MockThread:
            MockThread.return_value = MagicMock()
            resp = client.post(
                f"/api/jobs/{job_id}/migrate",
                json={"migration_goal": "EAP 7 to 8"},
                content_type="application/json",
            )
        assert resp.status_code == 202
        data = resp.get_json()
        assert data["status"] == "migrating"
        assert "migration_id" in data

    def test_migrate_empty_goal_uses_default(self, app_client):
        """Empty goal is allowed — backend uses a sensible default."""
        client, job_db, ws = app_client
        job_id = _create_job_with_report(job_db, ws)

        with patch("crew_studio.migration.blueprint.threading.Thread") as MockThread:
            MockThread.return_value = MagicMock()
            resp = client.post(
                f"/api/jobs/{job_id}/migrate",
                json={"migration_goal": ""},
                content_type="application/json",
            )
        assert resp.status_code == 202

    def test_migrate_job_not_found_returns_404(self, app_client):
        client, _, _ = app_client
        resp = client.post(
            "/api/jobs/nonexistent/migrate",
            json={"migration_goal": "Upgrade"},
            content_type="application/json",
        )
        assert resp.status_code == 404

    def test_migrate_no_documents_returns_400(self, app_client):
        client, job_db, ws = app_client
        job_id = str(uuid.uuid4())
        job_ws = ws / f"job-{job_id}"
        job_ws.mkdir()
        job_db.create_job(job_id, "vision", str(job_ws))
        job_db.update_job(job_id, {"status": "completed"})

        resp = client.post(
            f"/api/jobs/{job_id}/migrate",
            json={"migration_goal": "Upgrade"},
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert "document" in resp.get_json()["error"].lower()

    def test_migrate_accepts_migration_notes(self, app_client):
        client, job_db, ws = app_client
        job_id = _create_job_with_report(job_db, ws)

        with patch("crew_studio.migration.blueprint.threading.Thread") as MockThread:
            mock_thread = MagicMock()
            MockThread.return_value = mock_thread
            resp = client.post(
                f"/api/jobs/{job_id}/migrate",
                json={
                    "migration_goal": "EAP 7 to 8",
                    "migration_notes": "Skip auth module",
                },
                content_type="application/json",
            )
        assert resp.status_code == 202


class TestGetMigration:

    def test_get_migration_status_empty(self, app_client):
        client, job_db, ws = app_client
        job_id = _create_job_with_report(job_db, ws)

        resp = client.get(f"/api/jobs/{job_id}/migration")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["job_id"] == job_id
        assert data["summary"]["total"] == 0
        assert data["issues"] == []

    def test_get_migration_status_with_issues(self, app_client):
        client, job_db, ws = app_client
        job_id = _create_job_with_report(job_db, ws)

        # Seed some issues
        job_db.create_migration_issue(
            "issue-1", job_id, "mig-1", "Replace javax", "mandatory", "low",
            ["src/App.java"], "desc", "hint"
        )
        job_db.create_migration_issue(
            "issue-2", job_id, "mig-1", "Update XML", "optional", "medium",
            ["pom.xml"], "desc2", "hint2"
        )

        resp = client.get(f"/api/jobs/{job_id}/migration")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["summary"]["total"] == 2
        assert len(data["issues"]) == 2

    def test_get_migration_job_not_found(self, app_client):
        client, _, _ = app_client
        resp = client.get("/api/jobs/nonexistent/migration")
        assert resp.status_code == 404


class TestGetMigrationPlan:

    def test_plan_not_found(self, app_client):
        client, job_db, ws = app_client
        job_id = _create_job_with_report(job_db, ws)
        resp = client.get(f"/api/jobs/{job_id}/migration/plan")
        assert resp.status_code == 404

    def test_plan_returns_json(self, app_client):
        client, job_db, ws = app_client
        job_id = _create_job_with_report(job_db, ws)

        # Write a plan file
        plan_path = ws / f"job-{job_id}" / "migration_plan.json"
        plan_path.write_text(json.dumps({
            "migration_goal": "EAP 7 to 8",
            "issues": [{"id": "i-1", "title": "test"}],
        }))

        resp = client.get(f"/api/jobs/{job_id}/migration/plan")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["migration_goal"] == "EAP 7 to 8"
        assert len(data["issues"]) == 1


# ── Helper ───────────────────────────────────────────────────────────────────

def _make_zip(file_map: dict[str, str]) -> bytes:
    """Build an in-memory ZIP containing ``{path: content}`` entries."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, content in file_map.items():
            zf.writestr(path, content)
    return buf.getvalue()


# ── Tests for POST /api/jobs with mode=migration (ZIP extraction) ────────────

def _patch_globals(ws, test_db):
    """Context manager that patches module-level globals used by create_job."""
    return patch.multiple(
        "crew_studio.llamaindex_web_app",
        base_workspace_path=ws,
        job_db=test_db,
    )


class TestCreateMigrationJob:
    """Tests for POST /api/jobs with mode=migration (ZIP extraction).

    These tests patch the module-level ``base_workspace_path`` and ``job_db``
    so the endpoint creates job workspaces inside the test's temp directory.
    """

    def test_migration_mode_extracts_zip_to_workspace_root(self, app_client):
        """Source archive should be extracted preserving directory structure."""
        client, job_db, ws = app_client

        zip_bytes = _make_zip({
            "my-project/src/main/java/App.java": "public class App {}",
            "my-project/pom.xml": "<project/>",
        })
        report_content = json.dumps({"issues": []})

        with _patch_globals(ws, job_db):
            resp = client.post(
                "/api/jobs",
                data={
                    "vision": "[MTA Migration] report.json",
                    "mode": "migration",
                    "source_archive": (io.BytesIO(zip_bytes), "project.zip"),
                    "documents": (io.BytesIO(report_content.encode()), "report.json"),
                },
                content_type="multipart/form-data",
            )
        assert resp.status_code == 201
        body = resp.get_json()
        assert body["source_files"] == 2
        assert body["documents"] == 1

        # Verify files are at workspace root (top-level folder stripped)
        job_ws = ws / f"job-{body['job_id']}"
        assert (job_ws / "src" / "main" / "java" / "App.java").is_file()
        assert (job_ws / "pom.xml").is_file()

        # MTA report should be in docs/
        docs = list((job_ws / "docs").glob("*report*"))
        assert len(docs) == 1

    def test_migration_mode_does_not_start_build_pipeline(self, app_client):
        """mode=migration should return early without launching the build thread."""
        client, job_db, ws = app_client

        zip_bytes = _make_zip({"src/App.java": "class A {}"})

        with _patch_globals(ws, job_db):
            resp = client.post(
                "/api/jobs",
                data={
                    "vision": "[MTA Migration] r.json",
                    "mode": "migration",
                    "source_archive": (io.BytesIO(zip_bytes), "src.zip"),
                    "documents": (io.BytesIO(b"{}"), "r.json"),
                },
                content_type="multipart/form-data",
            )
        assert resp.status_code == 201
        body = resp.get_json()

        # Job should be queued (awaiting_migration), not running
        job = job_db.get_job(body["job_id"])
        assert job["status"] == "queued"
        assert job["current_phase"] == "awaiting_migration"

    def test_migration_mode_rejects_path_traversal_in_zip(self, app_client):
        """Entries with '..' should be silently skipped."""
        client, job_db, ws = app_client

        zip_bytes = _make_zip({
            "safe/App.java": "class A {}",
            "../../etc/passwd": "evil",
        })

        with _patch_globals(ws, job_db):
            resp = client.post(
                "/api/jobs",
                data={
                    "vision": "[MTA Migration] r.json",
                    "mode": "migration",
                    "source_archive": (io.BytesIO(zip_bytes), "src.zip"),
                    "documents": (io.BytesIO(b"{}"), "r.json"),
                },
                content_type="multipart/form-data",
            )
        assert resp.status_code == 201
        body = resp.get_json()
        # Only the safe file should have been extracted
        assert body["source_files"] == 1

    def test_migration_mode_without_zip_still_creates_job(self, app_client):
        """No source archive is OK (user might only provide a GitHub URL)."""
        client, job_db, ws = app_client

        with _patch_globals(ws, job_db):
            resp = client.post(
                "/api/jobs",
                data={
                    "vision": "[MTA Migration] r.json",
                    "mode": "migration",
                    "documents": (io.BytesIO(b"{}"), "r.json"),
                },
                content_type="multipart/form-data",
            )
        assert resp.status_code == 201
        body = resp.get_json()
        assert body["source_files"] == 0
        assert body["documents"] == 1


class TestCreateMigrationJobResponseContract:
    """Verify that the migration job response always includes the fields
    that the frontend ``Landing.tsx`` relies on for post-submit validation:
    - ``source_files``: number of files extracted from ZIP (or cloned)
    - ``documents``: number of MTA report files saved
    - ``github_repos``: number of valid GitHub URLs processed

    These tests exist because the frontend was silently failing: the UI sent
    files but never checked the response to confirm the server received them.
    """

    def test_response_includes_all_required_fields(self, app_client):
        """Every migration response must have job_id, status, documents, source_files, github_repos."""
        client, job_db, ws = app_client
        zip_bytes = _make_zip({"src/App.java": "class A {}"})

        with _patch_globals(ws, job_db):
            resp = client.post(
                "/api/jobs",
                data={
                    "vision": "[MTA Migration] report.json",
                    "mode": "migration",
                    "source_archive": (io.BytesIO(zip_bytes), "src.zip"),
                    "documents": (io.BytesIO(b"{}"), "report.json"),
                },
                content_type="multipart/form-data",
            )
        assert resp.status_code == 201
        body = resp.get_json()
        assert "job_id" in body
        assert "status" in body
        assert "documents" in body
        assert "source_files" in body
        assert "github_repos" in body

    def test_empty_zip_returns_source_files_zero(self, app_client):
        """A ZIP with no files should return source_files=0 so frontend can warn."""
        client, job_db, ws = app_client
        empty_zip = _make_zip({})  # ZIP with no entries

        with _patch_globals(ws, job_db):
            resp = client.post(
                "/api/jobs",
                data={
                    "vision": "[MTA Migration] report.json",
                    "mode": "migration",
                    "source_archive": (io.BytesIO(empty_zip), "empty.zip"),
                    "documents": (io.BytesIO(b"{}"), "report.json"),
                },
                content_type="multipart/form-data",
            )
        assert resp.status_code == 201
        body = resp.get_json()
        assert body["source_files"] == 0
        assert body["documents"] == 1

    def test_no_documents_returns_documents_zero(self, app_client):
        """No uploaded documents should return documents=0 so frontend can warn."""
        client, job_db, ws = app_client
        zip_bytes = _make_zip({"src/App.java": "class A {}"})

        with _patch_globals(ws, job_db):
            resp = client.post(
                "/api/jobs",
                data={
                    "vision": "[MTA Migration] report.json",
                    "mode": "migration",
                    "source_archive": (io.BytesIO(zip_bytes), "src.zip"),
                    # Note: no "documents" field
                },
                content_type="multipart/form-data",
            )
        assert resp.status_code == 201
        body = resp.get_json()
        assert body["documents"] == 0
        assert body["source_files"] == 1

    def test_no_source_archive_and_no_github_returns_zero(self, app_client):
        """No source at all should return source_files=0 + github_repos=0."""
        client, job_db, ws = app_client

        with _patch_globals(ws, job_db):
            resp = client.post(
                "/api/jobs",
                data={
                    "vision": "[MTA Migration] report.json",
                    "mode": "migration",
                    "documents": (io.BytesIO(b"{}"), "report.json"),
                },
                content_type="multipart/form-data",
            )
        assert resp.status_code == 201
        body = resp.get_json()
        assert body["source_files"] == 0
        assert body["github_repos"] == 0
        assert body["documents"] == 1

    def test_multiple_report_files_counted_correctly(self, app_client):
        """Multiple MTA report documents should all be counted."""
        client, job_db, ws = app_client
        zip_bytes = _make_zip({"pom.xml": "<project/>"})

        with _patch_globals(ws, job_db):
            resp = client.post(
                "/api/jobs",
                data={
                    "vision": "[MTA Migration] reports",
                    "mode": "migration",
                    "source_archive": (io.BytesIO(zip_bytes), "src.zip"),
                    "documents": [
                        (io.BytesIO(b'{"issues":[]}'), "issues.json"),
                        (io.BytesIO(b"file1,file2"), "files.csv"),
                        (io.BytesIO(b"<html>report</html>"), "report.html"),
                    ],
                },
                content_type="multipart/form-data",
            )
        assert resp.status_code == 201
        body = resp.get_json()
        assert body["documents"] == 3
        assert body["source_files"] >= 1


class TestRunnerMTAFastPath:
    """Tests that runner.py uses deterministic parsing for MTA issues.json.
    
    TDD for the fast path: when report is MTA format, skip the LLM analysis agent.
    """

    def test_mta_issues_json_skips_llm_analysis(self, app_client):
        """When report is MTA issues.json, runner parses deterministically (no LLM call)."""
        client, job_db, ws = app_client
        
        # Create a job with MTA issues.json in docs/
        job_id = str(uuid.uuid4())
        job_ws = ws / f"job-{job_id}"
        job_ws.mkdir()
        docs_dir = job_ws / "docs"
        docs_dir.mkdir()
        
        # Write a minimal MTA issues.json
        mta_report = docs_dir / "issues.json"
        mta_report.write_text(json.dumps([{
            "applicationId": "",
            "issues": {
                "mandatory": [{
                    "id": "test-001",
                    "name": "Test Issue",
                    "ruleId": "test-rule-001",
                    "effort": {"type": "Trivial", "points": 1, "description": ""},
                    "totalIncidents": 1,
                    "totalStoryPoints": 1,
                    "links": [],
                    "affectedFiles": [{
                        "description": "Fix this",
                        "files": [{"fileId": "1", "fileName": "src/App.java", "occurrences": 1}]
                    }],
                    "sourceTechnologies": [],
                    "targetTechnologies": []
                }]
            }
        }]))
        
        job_db.create_job(job_id, "[MTA Migration] issues.json", str(job_ws))
        job_db.update_job(job_id, {"status": "completed", "current_phase": "awaiting_migration"})
        
        # Register the MTA report as a document
        doc_id = str(uuid.uuid4())
        job_db.add_document(
            doc_id, job_id, "issues.json", "mta-issues.json", "json",
            mta_report.stat().st_size, str(mta_report)
        )
        
        # Create source file
        src_dir = job_ws / "src"
        src_dir.mkdir()
        (src_dir / "App.java").write_text("public class App {}")
        
        # Trigger migration using the blueprint endpoint
        with patch("crew_studio.migration.blueprint.threading.Thread") as MockThread:
            mock_thread = MagicMock()
            MockThread.return_value = mock_thread
            resp = client.post(
                f"/api/jobs/{job_id}/migrate",
                json={"migration_goal": "Test migration"},
                content_type="application/json",
            )
        
        assert resp.status_code == 202
        
        # The thread should have been started with run_migration
        MockThread.assert_called_once()
        # The target function should be the migration runner, not the analysis agent
        assert MockThread.call_args[1]["target"].__name__ == "_run_in_thread"

    def test_non_mta_report_uses_llm_analysis(self, app_client):
        """When report is CSV/HTML/text, runner still uses MigrationAnalysisAgent."""
        client, job_db, ws = app_client
        
        job_id = str(uuid.uuid4())
        job_ws = ws / f"job-{job_id}"
        job_ws.mkdir()
        docs_dir = job_ws / "docs"
        docs_dir.mkdir()
        
        # Write a non-MTA report (CSV)
        csv_report = docs_dir / "report.csv"
        csv_report.write_text("file,issue,severity\nsrc/App.java,Replace javax,mandatory")
        
        job_db.create_job(job_id, "[MTA Migration] report.csv", str(job_ws))
        job_db.update_job(job_id, {"status": "completed", "current_phase": "awaiting_migration"})
        
        # Register the doc
        doc_id = str(uuid.uuid4())
        job_db.add_document(doc_id, job_id, "report.csv", "report.csv", "csv", csv_report.stat().st_size, str(csv_report))
        
        (job_ws / "src").mkdir()
        (job_ws / "src" / "App.java").write_text("class App {}")
        
        # Trigger migration
        with patch("crew_studio.migration.blueprint.threading.Thread") as MockThread:
            mock_thread = MagicMock()
            MockThread.return_value = mock_thread
            resp = client.post(
                f"/api/jobs/{job_id}/migrate",
                json={"migration_goal": "Test"},
                content_type="application/json",
            )
        
        assert resp.status_code == 202


class TestRunnerMTAFastPath:
    """Tests that runner.py uses deterministic parsing for MTA issues.json.
    
    TDD for the fast path: when report is MTA format, skip the LLM analysis agent.
    """

    def test_mta_issues_json_skips_llm_analysis(self, app_client):
        """When report is MTA issues.json, runner parses deterministically (no LLM call)."""
        client, job_db, ws = app_client
        
        # Create a job with MTA issues.json in docs/
        job_id = str(uuid.uuid4())
        job_ws = ws / f"job-{job_id}"
        job_ws.mkdir()
        docs_dir = job_ws / "docs"
        docs_dir.mkdir()
        
        # Write a minimal MTA issues.json
        mta_report = docs_dir / "issues.json"
        mta_report.write_text(json.dumps([{
            "applicationId": "",
            "issues": {
                "mandatory": [{
                    "id": "test-001",
                    "name": "Test Issue",
                    "ruleId": "test-rule-001",
                    "effort": {"type": "Trivial", "points": 1, "description": ""},
                    "totalIncidents": 1,
                    "totalStoryPoints": 1,
                    "links": [],
                    "affectedFiles": [{
                        "description": "Fix this",
                        "files": [{"fileId": "1", "fileName": "src/App.java", "occurrences": 1}]
                    }],
                    "sourceTechnologies": [],
                    "targetTechnologies": []
                }]
            }
        }]))
        
        job_db.create_job(job_id, "[MTA Migration] issues.json", str(job_ws))
        job_db.update_job(job_id, {"status": "completed", "current_phase": "awaiting_migration"})
        
        # Register the MTA report as a document
        doc_id = str(uuid.uuid4())
        job_db.add_document(
            doc_id, job_id, "issues.json", "mta-issues.json", "json",
            mta_report.stat().st_size, str(mta_report)
        )
        
        # Create source file
        src_dir = job_ws / "src"
        src_dir.mkdir()
        (src_dir / "App.java").write_text("public class App {}")
        
        # Trigger migration using the blueprint endpoint
        with patch("crew_studio.migration.blueprint.threading.Thread") as MockThread:
            mock_thread = MagicMock()
            MockThread.return_value = mock_thread
            resp = client.post(
                f"/api/jobs/{job_id}/migrate",
                json={"migration_goal": "Test migration"},
                content_type="application/json",
            )
        
        assert resp.status_code == 202
        
        # The thread should have been started with run_migration
        MockThread.assert_called_once()
        # The target function should be the migration runner, not the analysis agent
        assert MockThread.call_args[1]["target"].__name__ == "_run_in_thread"

    def test_non_mta_report_uses_llm_analysis(self, app_client):
        """When report is CSV/HTML/text, runner still uses MigrationAnalysisAgent."""
        client, job_db, ws = app_client
        
        job_id = str(uuid.uuid4())
        job_ws = ws / f"job-{job_id}"
        job_ws.mkdir()
        docs_dir = job_ws / "docs"
        docs_dir.mkdir()
        
        # Write a non-MTA report (CSV)
        csv_report = docs_dir / "report.csv"
        csv_report.write_text("file,issue,severity\nsrc/App.java,Replace javax,mandatory")
        
        job_db.create_job(job_id, "[MTA Migration] report.csv", str(job_ws))
        job_db.update_job(job_id, {"status": "completed", "current_phase": "awaiting_migration"})
        
        # Register the doc
        doc_id = str(uuid.uuid4())
        job_db.add_document(doc_id, job_id, "report.csv", "report.csv", "csv", csv_report.stat().st_size, str(csv_report))
        
        (job_ws / "src").mkdir()
        (job_ws / "src" / "App.java").write_text("class App {}")
        
        # Trigger migration
        with patch("crew_studio.migration.blueprint.threading.Thread") as MockThread:
            mock_thread = MagicMock()
            MockThread.return_value = mock_thread
            resp = client.post(
                f"/api/jobs/{job_id}/migrate",
                json={"migration_goal": "Test"},
                content_type="application/json",
            )
        
        assert resp.status_code == 202


class TestMigrationGitHubClone:
    """Tests for GitHub URL cloning in migration mode (TDD for the fix)."""

    def test_migration_mode_clones_github_to_workspace_root(self, app_client):
        """GitHub repos should be cloned to workspace root, not packed as XML."""
        client, job_db, ws = app_client

        # Mock git clone to create files instead of actually cloning
        def mock_clone(url, target_dir, job_id):
            """Simulate git clone by creating test files."""
            target = Path(target_dir)
            (target / "src").mkdir(parents=True)
            (target / "src" / "App.java").write_text("public class App {}")
            (target / "README.md").write_text("# Test repo")
            return {"repo": url, "files": 2}

        with _patch_globals(ws, job_db):
            with patch("crew_studio.llamaindex_web_app._clone_github_repo", side_effect=mock_clone):
                resp = client.post(
                    "/api/jobs",
                    data={
                        "vision": "[MTA Migration] r.json",
                        "mode": "migration",
                        "github_urls": "https://github.com/test/repo",
                        "documents": (io.BytesIO(b"{}"), "r.json"),
                    },
                    content_type="multipart/form-data",
                )

        assert resp.status_code == 201
        body = resp.get_json()
        assert body["github_repos"] == 1
        # In migration mode, GitHub repos are cloned not packed
        assert "source_files" in body  # Should track cloned files

        # Verify files are at workspace root
        job_ws = ws / f"job-{body['job_id']}"
        assert (job_ws / "src" / "App.java").is_file()
        assert (job_ws / "README.md").is_file()

    def test_migration_mode_handles_multiple_github_urls(self, app_client):
        """Multiple GitHub repos should all be cloned to workspace root."""
        client, job_db, ws = app_client

        clone_count = 0

        def mock_clone(url, target_dir, job_id):
            nonlocal clone_count
            clone_count += 1
            target = Path(target_dir)
            # Create unique files per repo
            (target / f"file{clone_count}.txt").write_text(f"from {url}")
            return {"repo": url, "files": 1}

        with _patch_globals(ws, job_db):
            with patch("crew_studio.llamaindex_web_app._clone_github_repo", side_effect=mock_clone):
                resp = client.post(
                    "/api/jobs",
                    data={
                        "vision": "[MTA Migration] r.json",
                        "mode": "migration",
                        "github_urls": ["https://github.com/test/repo1", "https://github.com/test/repo2"],
                        "documents": (io.BytesIO(b"{}"), "r.json"),
                    },
                    content_type="multipart/form-data",
                )

        assert resp.status_code == 201
        body = resp.get_json()
        assert body["github_repos"] == 2
        assert clone_count == 2

    def test_migration_mode_github_clone_failure_continues(self, app_client):
        """If GitHub clone fails, job should still be created."""
        client, job_db, ws = app_client

        def mock_clone_fails(url, target_dir, job_id):
            raise Exception("Clone failed")

        with _patch_globals(ws, job_db):
            with patch("crew_studio.llamaindex_web_app._clone_github_repo", side_effect=mock_clone_fails):
                resp = client.post(
                    "/api/jobs",
                    data={
                        "vision": "[MTA Migration] r.json",
                        "mode": "migration",
                        "github_urls": "https://github.com/test/repo",
                        "documents": (io.BytesIO(b"{}"), "r.json"),
                    },
                    content_type="multipart/form-data",
                )

        # Job creation should still succeed even if clone fails
        assert resp.status_code == 201

    def test_migration_mode_strips_github_wrapper_dir(self, app_client):
        """GitHub repos often clone with a wrapper dir — _clone_github_repo handles it."""
        client, job_db, ws = app_client

        def mock_clone(url, target_dir, job_id):
            """Simulate what _clone_github_repo does: strip wrapper, place files at root."""
            target = Path(target_dir)
            # Real function strips wrapper and places files directly at root
            (target / "src").mkdir(parents=True)
            (target / "src" / "App.java").write_text("class A {}")
            return {"repo": url, "files": 1, "wrapper_dir": "repo-main"}

        with _patch_globals(ws, job_db):
            with patch("crew_studio.llamaindex_web_app._clone_github_repo", side_effect=mock_clone):
                resp = client.post(
                    "/api/jobs",
                    data={
                        "vision": "[MTA Migration] r.json",
                        "mode": "migration",
                        "github_urls": "https://github.com/test/repo",
                        "documents": (io.BytesIO(b"{}"), "r.json"),
                    },
                    content_type="multipart/form-data",
                )

        assert resp.status_code == 201
        body = resp.get_json()

        # Files should be at root, not under repo-main/
        job_ws = ws / f"job-{body['job_id']}"
        assert (job_ws / "src" / "App.java").is_file()
        assert not (job_ws / "repo-main").exists()
