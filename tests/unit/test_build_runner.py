"""
TDD tests for shared build pipeline (no duplication).

- run_build_pipeline(job_id, workspace_path, vision, ...) is the single place that
  creates SoftwareDevWorkflow and runs it.
- run_job_async uses run_build_pipeline (does not duplicate workflow creation).
- After refactor success, the refactor flow calls run_build_pipeline with
  workspace_path = job_workspace / "refactored" and refactor-derived vision.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "agent"))
sys.path.insert(0, str(root / "agent" / "src"))


class TestRunBuildPipelineShared:
    """run_build_pipeline is the single implementation for running the workflow."""

    def test_run_build_pipeline_exists_and_calls_workflow_with_workspace_and_vision(self, tmp_path):
        """run_build_pipeline creates SoftwareDevWorkflow with given workspace_path and vision."""
        from crew_studio import build_runner

        MockWF = MagicMock(return_value=MagicMock(run=MagicMock(return_value={"status": "completed", "task_validation": {"valid": True}})))
        with patch("src.llamaindex_crew.workflows.software_dev_workflow.SoftwareDevWorkflow", MockWF):
            mock_cb = MagicMock()
            mock_db = MagicMock()
            result = build_runner.run_build_pipeline(
                job_id="j1",
                workspace_path=tmp_path,
                vision="Build the app per target_architecture.md",
                config=MagicMock(),
                progress_callback=mock_cb,
                job_db=mock_db,
            )
            MockWF.assert_called_once()
            call_kw = MockWF.call_args[1]
            assert call_kw["workspace_path"] == tmp_path
            assert call_kw["vision"] == "Build the app per target_architecture.md"
            assert result.get("status") == "completed"

    def test_run_job_async_uses_run_build_pipeline_no_duplication(self, tmp_path):
        """run_job_async delegates to run_build_pipeline; no inline SoftwareDevWorkflow."""
        import tempfile
        import uuid
        from crew_studio.job_database import JobDatabase
        from crew_studio.llamaindex_web_app import app, run_job_async

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "test.db"
            ws_path = Path(td) / "workspace"
            ws_path.mkdir()
            job_db = JobDatabase(db_path)
            app.config["JOB_DB"] = job_db
            app.config["WORKSPACE_PATH"] = str(ws_path)

            job_id = str(uuid.uuid4())
            job_ws = ws_path / f"job-{job_id}"
            job_ws.mkdir()
            job_db.create_job(job_id, "Build API", str(job_ws))
            job_db.update_job(job_id, {"status": "pending", "current_phase": "starting"})

            import crew_studio.llamaindex_web_app as web_app_module
            original_db = web_app_module.job_db
            web_app_module.job_db = job_db

            with patch("crew_studio.build_runner.run_build_pipeline") as mock_run_build:
                mock_run_build.return_value = {
                    "status": "completed",
                    "task_validation": {"valid": True},
                    "budget_report": {},
                }
                run_job_async(job_id, "Build API", None)

                mock_run_build.assert_called_once()
                call_kw = mock_run_build.call_args[1]
                assert call_kw["job_id"] == job_id
                assert call_kw["workspace_path"] == job_ws
                assert "Build API" in call_kw["vision"]
            web_app_module.job_db = original_db


class TestRefactorRunsBuildOnRefactored:
    """After refactor completes successfully, build pipeline runs on refactored/."""

    @patch("crew_studio.refactor.blueprint.threading.Thread")
    def test_refactor_success_invokes_build_pipeline_with_refactored_path(
        self, mock_thread, tmp_path
    ):
        """When refactor thread completes, run_build_pipeline is called with workspace_path = .../refactored."""
        from flask import Flask
        from crew_studio.refactor.blueprint import refactor_bp

        mock_job_db = MagicMock()
        job_id = "refactor-job-1"
        job_ws = tmp_path / f"job-{job_id}"
        job_ws.mkdir()
        (job_ws / "refactored").mkdir()
        mock_job_db.get_job.return_value = {
            "id": job_id,
            "status": "queued",
            "workspace_path": str(job_ws),
        }

        app = Flask(__name__)
        app.config["JOB_DB"] = mock_job_db
        app.config["WORKSPACE_PATH"] = str(tmp_path)
        app.register_blueprint(refactor_bp)

        with patch("crew_studio.refactor.runner.run_refactor_job") as mock_run_refactor:
            mock_run_refactor.return_value = {"total_tasks": 1, "completed_tasks": 1, "failed_tasks": 0}
            with patch("crew_studio.build_runner.run_build_pipeline") as mock_build:
                mock_build.return_value = {
                    "status": "completed",
                    "task_validation": {"valid": True},
                    "budget_report": {},
                }
                client = app.test_client()
                r = client.post(
                    f"/api/jobs/{job_id}/refactor",
                    json={"target_stack": "Java 17"},
                )
                assert r.status_code == 202
                target_func = mock_thread.call_args[1]["target"]
                target_func()

                mock_run_refactor.assert_called_once()
                mock_build.assert_called_once()
                call_kw = mock_build.call_args[1]
                workspace_path = call_kw["workspace_path"]
                assert str(workspace_path).endswith("refactored"), (
                    f"run_build_pipeline must be called with workspace_path ending in 'refactored', got {workspace_path}"
                )
                assert "target_architecture" in call_kw["vision"].lower() or "refactor" in call_kw["vision"].lower(), (
                    f"Vision should reference refactor context, got: {call_kw['vision'][:200]}"
                )
