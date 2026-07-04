"""Regression tests for plan refinement during pending_review."""
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from llamaindex_crew.orchestrator.state_machine import ProjectState, TransitionContext
from llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow


@pytest.fixture
def workspace():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


def _workflow_at_tech_architect(workspace: Path) -> SoftwareDevWorkflow:
    wf = SoftwareDevWorkflow(
        project_id="job-refine-1",
        workspace_path=workspace,
        vision="Build a todo app",
    )
    sm = wf.state_machine
    sm.transition(ProjectState.PRODUCT_OWNER, TransitionContext(phase="product_owner", data={}))
    sm.transition(ProjectState.DESIGNER, TransitionContext(phase="designer", data={}))
    sm.transition(ProjectState.TECH_ARCHITECT, TransitionContext(phase="tech_architect", data={}))
    assert sm.get_current_state() == ProjectState.TECH_ARCHITECT
    return wf


class TestRefinePlan:
    def test_rolls_back_state_before_re_running_phases(self, workspace):
        wf = _workflow_at_tech_architect(workspace)
        phases_run: list[str] = []

        def _capture_phase(name, fn):
            phases_run.append(name)
            return f"{name}-done"

        with patch.object(wf, "_run_phase_with_retry", side_effect=_capture_phase):
            result = wf.refine_plan("Add pagination to the list view")

        rollback = [
            h for h in wf.state_machine.get_state_history()
            if h.get("context", {}) and h["context"].get("type") == "rollback"
        ]
        assert rollback
        assert rollback[-1]["to_state"] == ProjectState.PRODUCT_OWNER.value
        assert phases_run == ["product_owner", "designer", "tech_architect"]
        assert result["status"] == "pending_review"
        assert result["feedback_rounds"] == 1
