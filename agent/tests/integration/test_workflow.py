"""
Integration tests for SoftwareDevWorkflow
"""
import pytest
import tempfile
import shutil
from pathlib import Path
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow


@pytest.fixture
def temp_workspace():
    """Create temporary workspace"""
    workspace = tempfile.mkdtemp()
    yield Path(workspace)
    shutil.rmtree(workspace)


@pytest.mark.integration
def test_workflow_initialization(temp_workspace):
    """Test workflow can be initialized"""
    workflow = SoftwareDevWorkflow(
        project_id="test_project",
        workspace_path=temp_workspace,
        vision="Create a simple calculator"
    )
    
    assert workflow.project_id == "test_project"
    assert workflow.workspace_path == temp_workspace
    assert workflow.vision == "Create a simple calculator"
    assert workflow.state_machine is not None
    assert workflow.task_manager is not None
    assert workflow.budget_tracker is not None


@pytest.mark.integration
def test_workflow_state_transitions(temp_workspace):
    """Test workflow state transitions"""
    workflow = SoftwareDevWorkflow(
        project_id="test_project",
        workspace_path=temp_workspace,
        vision="Test vision"
    )
    
    # Initial state should be META
    assert workflow.state_machine.get_current_state().value == "meta"
    
    # Note: Full workflow execution requires LLM API keys
    # This test just verifies the workflow structure
