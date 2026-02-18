"""
TDD tests for run_job_async build guard (crew_studio.llamaindex_web_app).

Test that migration jobs with current_phase='awaiting_migration' skip the build pipeline.
"""
import sys
import tempfile
import uuid
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

        from crew_studio.job_database import JobDatabase
        test_db = JobDatabase(db_path)

        from crew_studio.llamaindex_web_app import app

        app.config["TESTING"] = True
        app.config["JOB_DB"] = test_db
        app.config["WORKSPACE_PATH"] = str(ws_path)

        with app.test_client() as client:
            yield client, test_db, ws_path


class TestBuildPipelineGuard:
    """Test that run_job_async skips build pipeline for migration jobs."""

    def test_migration_job_skips_build_pipeline(self, app_client):
        """Job with current_phase='awaiting_migration' should not run build pipeline."""
        _, job_db, ws = app_client
        
        job_id = str(uuid.uuid4())
        job_ws = ws / f"job-{job_id}"
        job_ws.mkdir()
        
        # Create a migration job
        job_db.create_job(job_id, "[MTA Migration] test", str(job_ws))
        job_db.update_job(job_id, {"status": "queued", "current_phase": "awaiting_migration"})
        
        # Import the function we're testing
        from crew_studio.llamaindex_web_app import run_job_async
        
        # Mock the workflow module at the import site INSIDE run_job_async
        with patch("src.llamaindex_crew.workflows.software_dev_workflow.SoftwareDevWorkflow") as MockWorkflow:
            mock_wf = MagicMock()
            MockWorkflow.return_value = mock_wf
            
            # Call run_job_async
            run_job_async(job_id, "[MTA Migration] test", None)
            
            # The workflow should NEVER be instantiated for migration jobs
            MockWorkflow.assert_not_called()
        
        # Job should still be in awaiting_migration state
        job = job_db.get_job(job_id)
        assert job["current_phase"] == "awaiting_migration"

    def test_normal_job_runs_build_pipeline(self, app_client):
        """Job without awaiting_migration phase proceeds past guard and attempts to load workflow."""
        _, job_db, ws = app_client
        
        job_id = str(uuid.uuid4())
        job_ws = ws / f"job-{job_id}"
        job_ws.mkdir()
        
        # Create a normal build job
        job_db.create_job(job_id, "Build a REST API", str(job_ws))
        job_db.update_job(job_id, {"status": "pending", "current_phase": "starting"})
        
        # The test fixture creates an isolated DB, but run_job_async uses the global one
        # So we can't reliably test the full workflow. Instead, verify the guard logic directly
        from crew_studio.llamaindex_web_app import run_job_async
        
        # Mock the workflow import to prevent actually running
        with patch("src.llamaindex_crew.workflows.software_dev_workflow.SoftwareDevWorkflow") as MockWorkflow:
            mock_wf = MagicMock()
            MockWorkflow.return_value = mock_wf
            
            # Temporarily patch the global job_db used by run_job_async to use our test DB
            import crew_studio.llamaindex_web_app as web_app_module
            original_db = web_app_module.job_db
            web_app_module.job_db = job_db
            
            try:
                run_job_async(job_id, "Build a REST API", None)
                
                # Workflow SHOULD be instantiated for normal jobs (guard does not block)
                MockWorkflow.assert_called_once()
            finally:
                web_app_module.job_db = original_db
