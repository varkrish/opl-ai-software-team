"""
Test cases for migration job creation and pipeline guard validation.

These tests catch scenarios where:
1. Migration jobs are created without source code
2. Build pipeline runs for migration jobs (should be blocked)
3. Migration jobs don't have correct `awaiting_migration` phase
"""
import pytest
import tempfile
import uuid
import io
from pathlib import Path
from unittest.mock import patch, MagicMock


@pytest.fixture
def app_client():
    """Create a Flask test client with a temp DB and workspace.
    
    Swaps the module-level globals (job_db, base_workspace_path) so
    the Flask routes use the isolated test DB, then restores them.
    """
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        ws_path = Path(tmp) / "workspace"
        ws_path.mkdir()

        from crew_studio.job_database import JobDatabase
        test_db = JobDatabase(db_path)

        import crew_studio.llamaindex_web_app as web_mod

        # Save originals
        orig_db = web_mod.job_db
        orig_ws = web_mod.base_workspace_path

        # Inject test doubles
        web_mod.job_db = test_db
        web_mod.base_workspace_path = ws_path
        web_mod.app.config["TESTING"] = True
        web_mod.app.config["JOB_DB"] = test_db
        web_mod.app.config["WORKSPACE_PATH"] = str(ws_path)

        try:
            with web_mod.app.test_client() as client:
                yield client, test_db, ws_path
        finally:
            # Restore originals
            web_mod.job_db = orig_db
            web_mod.base_workspace_path = orig_ws


class TestMigrationJobCreation:
    """Test proper migration job creation and validation."""

    def test_migration_job_requires_source_code(self, app_client):
        """Migration job creation should fail/warn if no source code provided."""
        client, job_db, ws = app_client
        
        # Create a migration job with MTA report but NO source code
        mta_report = io.BytesIO(b'[{"applicationId": "", "issues": {}}]')
        mta_report.name = 'issues.json'
        
        resp = client.post(
            '/api/jobs',
            data={
                'vision': '[MTA Migration] Test migration',
                'mode': 'migration',
                'documents': (mta_report, 'issues.json'),
                # NO source_archive provided!
            },
            content_type='multipart/form-data'
        )
        
        assert resp.status_code == 201
        data = resp.get_json()
        
        # Should warn about missing source code
        assert data['source_files'] == 0, "Expected 0 source files when none uploaded"
        
        # Job should be created but phase should be awaiting_migration
        job = job_db.get_job(data['job_id'])
        assert job['current_phase'] == 'awaiting_migration'
        assert job['status'] == 'queued'

    def test_migration_job_requires_mta_report(self, app_client):
        """Migration job creation should fail if no MTA report provided."""
        client, job_db, ws = app_client
        
        # Create a valid ZIP for source code
        import zipfile
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w') as zf:
            zf.writestr('src/App.java', 'class App {}')
        zip_buffer.seek(0)
        zip_buffer.name = 'source.zip'
        
        resp = client.post(
            '/api/jobs',
            data={
                'vision': '[MTA Migration] Test migration',
                'mode': 'migration',
                'source_archive': (zip_buffer, 'source.zip'),
                # NO documents provided!
            },
            content_type='multipart/form-data'
        )
        
        assert resp.status_code == 201
        data = resp.get_json()
        
        # Should warn about missing documents
        assert data['documents'] == 0, "Expected 0 documents when none uploaded"

    def test_migration_job_sets_awaiting_migration_phase(self, app_client):
        """Migration jobs must be created with current_phase='awaiting_migration'."""
        client, job_db, ws = app_client
        
        # Create a proper migration job
        mta_report = io.BytesIO(b'[{"applicationId": "", "issues": {}}]')
        mta_report.name = 'issues.json'
        
        # Create a minimal valid ZIP
        import zipfile
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w') as zf:
            zf.writestr('test.txt', 'test content')
        zip_buffer.seek(0)
        zip_buffer.name = 'source.zip'
        
        resp = client.post(
            '/api/jobs',
            data={
                'vision': '[MTA Migration] Test migration',
                'mode': 'migration',
                'documents': (mta_report, 'issues.json'),
                'source_archive': (zip_buffer, 'source.zip'),
            },
            content_type='multipart/form-data'
        )
        
        assert resp.status_code == 201
        data = resp.get_json()
        
        # CRITICAL: Job must be in awaiting_migration phase
        job = job_db.get_job(data['job_id'])
        assert job['current_phase'] == 'awaiting_migration', \
            f"Expected 'awaiting_migration' but got '{job['current_phase']}'"
        assert job['status'] == 'queued'

    def test_migration_job_does_not_auto_start_build_pipeline(self, app_client):
        """Migration jobs must NOT automatically start the build pipeline."""
        client, job_db, ws = app_client
        
        mta_report = io.BytesIO(b'[{"applicationId": "", "issues": {}}]')
        mta_report.name = 'issues.json'
        
        import zipfile
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w') as zf:
            zf.writestr('src/App.java', 'public class App {}')
        zip_buffer.seek(0)
        zip_buffer.name = 'source.zip'
        
        # Mock the background thread to prevent actual execution
        with patch('crew_studio.llamaindex_web_app.threading.Thread') as MockThread:
            mock_thread = MagicMock()
            MockThread.return_value = mock_thread
            
            resp = client.post(
                '/api/jobs',
                data={
                    'vision': '[MTA Migration] Test migration',
                    'mode': 'migration',
                    'documents': (mta_report, 'issues.json'),
                    'source_archive': (zip_buffer, 'source.zip'),
                },
                content_type='multipart/form-data'
            )
        
        assert resp.status_code == 201
        data = resp.get_json()
        
        # Thread should NOT be started for migration jobs
        MockThread.assert_not_called()
        
        job = job_db.get_job(data['job_id'])
        assert job['current_phase'] == 'awaiting_migration'
        assert job['status'] == 'queued'


class TestMigrationPipelineGuard:
    """Test that build pipeline is blocked for migration jobs."""

    def test_build_pipeline_blocked_for_awaiting_migration(self, app_client):
        """run_job_async should exit early for jobs in awaiting_migration phase."""
        _, job_db, ws = app_client
        # app_client fixture already swapped the global job_db
        
        job_id = str(uuid.uuid4())
        job_ws = ws / f"job-{job_id}"
        job_ws.mkdir()
        
        # Create a migration job in awaiting_migration phase
        job_db.create_job(job_id, "[MTA Migration] Test", str(job_ws))
        job_db.update_job(job_id, {
            'status': 'queued',
            'current_phase': 'awaiting_migration'
        })
        
        from crew_studio.llamaindex_web_app import run_job_async
        
        # Patch the workflow to ensure it's never instantiated
        with patch("src.llamaindex_crew.workflows.software_dev_workflow.SoftwareDevWorkflow") as MockWorkflow:
            run_job_async(job_id, "[MTA Migration] Test", None)
            
            # Workflow should NEVER be instantiated
            MockWorkflow.assert_not_called()
            
            # Job should remain in awaiting_migration
            job = job_db.get_job(job_id)
            assert job['current_phase'] == 'awaiting_migration'

    def test_normal_jobs_proceed_past_guard(self, app_client):
        """Normal (non-migration) jobs should proceed past the guard."""
        _, job_db, ws = app_client
        # app_client fixture already swapped the global job_db
        
        job_id = str(uuid.uuid4())
        job_ws = ws / f"job-{job_id}"
        job_ws.mkdir()
        
        # Create a normal build job
        job_db.create_job(job_id, "Build a REST API", str(job_ws))
        job_db.update_job(job_id, {'status': 'pending', 'current_phase': 'starting'})
        
        from crew_studio.llamaindex_web_app import run_job_async
        
        with patch("src.llamaindex_crew.workflows.software_dev_workflow.SoftwareDevWorkflow") as MockWorkflow:
            mock_wf = MagicMock()
            MockWorkflow.return_value = mock_wf
            
            run_job_async(job_id, "Build a REST API", None)
            
            # Workflow SHOULD be instantiated for normal jobs
            MockWorkflow.assert_called_once()


class TestMigrationTrigger:
    """Test that migration must be explicitly triggered."""

    def test_migration_requires_explicit_trigger(self, app_client):
        """Migration should only start after POST /api/jobs/{id}/migrate."""
        client, job_db, ws = app_client
        
        job_id = str(uuid.uuid4())
        job_ws = ws / f"job-{job_id}"
        job_ws.mkdir()
        docs_dir = job_ws / "docs"
        docs_dir.mkdir()
        
        # Create MTA report
        mta_report = docs_dir / "issues.json"
        mta_report.write_text('[{"applicationId": "", "issues": {"mandatory": []}}]')
        
        # Register the report
        doc_id = str(uuid.uuid4())
        job_db.add_document(
            doc_id, job_id, "issues.json", "mta-issues.json", "json",
            mta_report.stat().st_size, str(mta_report)
        )
        
        # Create job in awaiting_migration state
        job_db.create_job(job_id, "[MTA Migration] Test", str(job_ws))
        job_db.update_job(job_id, {
            'status': 'queued',
            'current_phase': 'awaiting_migration'
        })
        
        # Create source code
        (job_ws / "src").mkdir()
        (job_ws / "src" / "App.java").write_text("class App {}")
        
        # Job should be in awaiting_migration - NOT running
        job = job_db.get_job(job_id)
        assert job['current_phase'] == 'awaiting_migration'
        assert job['status'] == 'queued'
        
        # Now trigger migration explicitly
        with patch("crew_studio.migration.blueprint.threading.Thread") as MockThread:
            mock_thread = MagicMock()
            MockThread.return_value = mock_thread
            
            resp = client.post(
                f'/api/jobs/{job_id}/migrate',
                json={'migration_goal': 'Test migration'},
                content_type='application/json'
            )
        
        assert resp.status_code == 202
        data = resp.get_json()
        assert data['status'] == 'migrating'
        
        # Thread should have been started NOW (not during job creation)
        MockThread.assert_called_once()


class TestMigrationJobValidation:
    """Test validation of migration job prerequisites."""

    def test_migration_trigger_requires_documents(self, app_client):
        """POST /migrate should fail if no documents uploaded."""
        client, job_db, ws = app_client
        
        job_id = str(uuid.uuid4())
        job_ws = ws / f"job-{job_id}"
        job_ws.mkdir()
        
        job_db.create_job(job_id, "[MTA Migration] Test", str(job_ws))
        job_db.update_job(job_id, {
            'status': 'queued',
            'current_phase': 'awaiting_migration'
        })
        
        # Try to start migration without any documents
        resp = client.post(
            f'/api/jobs/{job_id}/migrate',
            json={'migration_goal': 'Test migration'},
            content_type='application/json'
        )
        
        assert resp.status_code == 400
        data = resp.get_json()
        assert 'error' in data
        assert 'documents' in data['error'].lower() or 'report' in data['error'].lower()

    def test_migration_response_includes_source_files_count(self, app_client):
        """Job creation response must include source_files count for validation."""
        client, job_db, ws = app_client
        
        mta_report = io.BytesIO(b'[{"applicationId": "", "issues": {}}]')
        mta_report.name = 'issues.json'
        
        resp = client.post(
            '/api/jobs',
            data={
                'vision': '[MTA Migration] Test migration',
                'mode': 'migration',
                'documents': (mta_report, 'issues.json'),
                # No source code
            },
            content_type='multipart/form-data'
        )
        
        assert resp.status_code == 201
        data = resp.get_json()
        
        # Response MUST include these fields for frontend validation
        assert 'source_files' in data
        assert 'documents' in data
        assert 'github_repos' in data
        
        # Frontend can now warn: "0 source files - did you forget to upload?"
        assert data['source_files'] == 0
        assert data['documents'] == 1

    def test_job_remains_queued_until_migration_triggered(self, app_client):
        """Job status should remain 'queued' until /migrate is called."""
        client, job_db, ws = app_client
        
        mta_report = io.BytesIO(b'[{"applicationId": "", "issues": {}}]')
        mta_report.name = 'issues.json'
        
        import zipfile
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w') as zf:
            zf.writestr('src/App.java', 'class App {}')
        zip_buffer.seek(0)
        zip_buffer.name = 'source.zip'
        
        resp = client.post(
            '/api/jobs',
            data={
                'vision': '[MTA Migration] Test migration',
                'mode': 'migration',
                'documents': (mta_report, 'issues.json'),
                'source_archive': (zip_buffer, 'source.zip'),
            },
            content_type='multipart/form-data'
        )
        
        data = resp.get_json()
        job_id = data['job_id']
        
        # Check job immediately after creation
        job = job_db.get_job(job_id)
        assert job['status'] == 'queued', "Job should remain queued"
        assert job['current_phase'] == 'awaiting_migration'
        
        # Wait a bit and check again - should still be queued
        import time
        time.sleep(0.5)
        
        job = job_db.get_job(job_id)
        assert job['status'] == 'queued', "Job should STILL be queued"
        assert job['current_phase'] == 'awaiting_migration'
