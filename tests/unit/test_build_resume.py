"""
BDD/TDD tests for "Resume job from where it left off".

Scenarios:
- Workflow loads persisted artifacts (user_stories, design_spec, tech_stack, backstories)
  when resume=True and runs only phases from current state.
- run_build_pipeline(resume=True) passes resume to workflow.run().
- run_job_async(..., resume=True) passes resume to run_build_pipeline().
- POST /api/jobs/<id>/restart with body {"resume": true} triggers resume path for build jobs.
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "agent"))
sys.path.insert(0, str(root / "agent" / "src"))


# ═══════════════════════════════════════════════════════════════════════════════
# Workflow: _load_phase_artifacts loads persisted files
# ═══════════════════════════════════════════════════════════════════════════════

class TestLoadPhaseArtifacts:
    """Given workspace with persisted artifacts, _load_phase_artifacts populates workflow."""

    def test_loads_user_stories_design_spec_tech_stack_from_workspace(self, tmp_path):
        """Load user_stories.md, design_spec.md, tech_stack.md when present."""
        (tmp_path / "user_stories.md").write_text("# User stories\n- As a user I want X")
        (tmp_path / "design_spec.md").write_text("# Design\n- Wireframes")
        (tmp_path / "tech_stack.md").write_text("# Stack\n- React")

        from src.llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow
        workflow = SoftwareDevWorkflow(
            project_id="test-proj",
            workspace_path=tmp_path,
            vision="Build app",
            config=MagicMock(),
        )
        workflow._load_phase_artifacts()

        assert workflow.user_stories is not None and "User stories" in workflow.user_stories
        assert workflow.design_spec is not None and "Design" in workflow.design_spec
        assert workflow.tech_stack is not None and "Stack" in workflow.tech_stack

    def test_loads_agent_backstories_json_when_present(self, tmp_path):
        """Load agent_backstories.json so frontend phase has backstory on resume."""
        backstories = {"frontend_developer": "You are a UI expert.", "product_owner": "You write stories."}
        (tmp_path / "agent_backstories.json").write_text(json.dumps(backstories))

        from src.llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow
        workflow = SoftwareDevWorkflow(
            project_id="test-proj",
            workspace_path=tmp_path,
            vision="Build app",
            config=MagicMock(),
        )
        workflow._load_phase_artifacts()

        assert workflow.agent_backstories == backstories
        assert workflow.agent_backstories.get("frontend_developer") == "You are a UI expert."


# ═══════════════════════════════════════════════════════════════════════════════
# Workflow: run(resume=True) runs only from current state
# ═══════════════════════════════════════════════════════════════════════════════

class TestWorkflowRunResume:
    """When resume=True, workflow runs only phases from current state onward."""

    def test_resume_from_frontend_runs_only_frontend_phase(self, tmp_path):
        """Given state=FRONTEND, run(resume=True) should run only frontend then complete."""
        state_file = tmp_path / "state_test-proj.json"
        state_file.write_text(json.dumps({
            "current_state": "frontend",
            "project_id": "test-proj",
            "timestamp": 0,
        }))
        (tmp_path / "user_stories.md").write_text("stories")
        (tmp_path / "design_spec.md").write_text("design")
        (tmp_path / "tech_stack.md").write_text("stack")
        (tmp_path / "agent_backstories.json").write_text(json.dumps({"frontend_developer": "UI expert"}))

        from src.llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow
        workflow = SoftwareDevWorkflow(
            project_id="test-proj",
            workspace_path=tmp_path,
            vision="Build app",
            config=MagicMock(),
        )
        with patch.object(workflow, "run_meta_phase") as mock_meta, \
             patch.object(workflow, "run_product_owner_phase", MagicMock()) as mock_po, \
             patch.object(workflow, "run_designer_phase", MagicMock()) as mock_designer, \
             patch.object(workflow, "run_tech_architect_phase", MagicMock()) as mock_arch, \
             patch.object(workflow, "run_development_phase", MagicMock()) as mock_dev, \
             patch.object(workflow, "run_frontend_phase", return_value="ok") as mock_frontend, \
             patch.object(workflow.task_manager, "validate_all_tasks_completed", return_value={"valid": True, "incomplete_tasks": [], "failed_tasks": []}), \
             patch.object(workflow.task_manager, "get_incomplete_tasks", return_value=[]):
            workflow.run(resume=True)
            # Only frontend runs; meta/product_owner/designer/arch/dev should not be called
            mock_meta.assert_not_called()
            mock_po.assert_not_called()
            mock_designer.assert_not_called()
            mock_arch.assert_not_called()
            mock_dev.assert_not_called()
            mock_frontend.assert_called_once()

    def test_run_resume_false_runs_full_workflow(self, tmp_path):
        """When resume=False, workflow.run() calls run_meta_phase first."""
        from src.llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow
        workflow = SoftwareDevWorkflow(
            project_id="test-proj",
            workspace_path=tmp_path,
            vision="Build app",
            config=MagicMock(),
        )
        with patch.object(workflow, "run_meta_phase", return_value={}) as mock_meta, \
             patch.object(workflow, "run_product_owner_phase", MagicMock()), \
             patch.object(workflow, "run_designer_phase", MagicMock()), \
             patch.object(workflow, "run_tech_architect_phase", MagicMock()), \
             patch.object(workflow, "run_development_phase", MagicMock()), \
             patch.object(workflow, "run_frontend_phase", MagicMock()), \
             patch.object(workflow.task_manager, "validate_all_tasks_completed", return_value={"valid": True, "incomplete_tasks": [], "failed_tasks": []}), \
             patch.object(workflow.task_manager, "get_incomplete_tasks", return_value=[]), \
             patch.object(workflow.state_machine, "transition", MagicMock()):
            try:
                workflow.run(resume=False)
            except Exception:
                pass
            mock_meta.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════════
# run_build_pipeline passes resume to workflow.run()
# ═══════════════════════════════════════════════════════════════════════════════

class TestRunBuildPipelineResume:
    """run_build_pipeline(resume=True) calls workflow.run(resume=True)."""

    def test_run_build_pipeline_calls_workflow_run_with_resume_true(self, tmp_path):
        """When resume=True, workflow.run(resume=True) is invoked."""
        from crew_studio import build_runner
        MockWF = MagicMock(return_value=MagicMock(
            run=MagicMock(return_value={"status": "completed", "task_validation": {"valid": True}})
        ))
        with patch("src.llamaindex_crew.workflows.software_dev_workflow.SoftwareDevWorkflow", MockWF):
            build_runner.run_build_pipeline(
                job_id="j1",
                workspace_path=tmp_path,
                vision="Build app",
                config=MagicMock(),
                progress_callback=MagicMock(),
                job_db=MagicMock(),
                resume=True,
            )
            MockWF.return_value.run.assert_called_once_with(resume=True)

    def test_run_build_pipeline_default_resume_false(self, tmp_path):
        """When resume not passed, workflow.run() is called with resume=False (default)."""
        from crew_studio import build_runner
        MockWF = MagicMock(return_value=MagicMock(
            run=MagicMock(return_value={"status": "completed", "task_validation": {"valid": True}})
        ))
        with patch("src.llamaindex_crew.workflows.software_dev_workflow.SoftwareDevWorkflow", MockWF):
            build_runner.run_build_pipeline(
                job_id="j1",
                workspace_path=tmp_path,
                vision="Build app",
                config=MagicMock(),
                progress_callback=MagicMock(),
                job_db=MagicMock(),
            )
            MockWF.return_value.run.assert_called_once()
            call_kw = MockWF.return_value.run.call_args[1]
            assert call_kw.get("resume") is False or "resume" not in call_kw


# ═══════════════════════════════════════════════════════════════════════════════
# run_job_async passes resume to run_build_pipeline
# ═══════════════════════════════════════════════════════════════════════════════

class TestRunJobAsyncResume:
    """run_job_async(..., resume=True) passes resume to run_build_pipeline."""

    def test_run_job_async_passes_resume_to_run_build_pipeline(self, tmp_path):
        """When run_job_async is called with resume=True, run_build_pipeline gets resume=True."""
        import tempfile
        import uuid
        from crew_studio.job_database import JobDatabase
        from crew_studio.llamaindex_web_app import run_job_async
        import crew_studio.llamaindex_web_app as web_app_module

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "test.db"
            ws_path = Path(td) / "workspace"
            ws_path.mkdir()
            job_db = JobDatabase(db_path)
            web_app_module.job_db = job_db
            job_id = str(uuid.uuid4())
            job_ws = ws_path / f"job-{job_id}"
            job_ws.mkdir()
            job_db.create_job(job_id, "Build API", str(job_ws))
            job_db.update_job(job_id, {"status": "pending", "current_phase": "starting"})

            with patch("crew_studio.build_runner.run_build_pipeline") as mock_run_build:
                mock_run_build.return_value = {
                    "status": "completed",
                    "task_validation": {"valid": True},
                    "budget_report": {},
                }
                run_job_async(job_id, "Build API", None, resume=True)
                mock_run_build.assert_called_once()
                assert mock_run_build.call_args[1].get("resume") is True
