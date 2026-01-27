"""
Performance tests for workflow execution
"""
import pytest
import time
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


@pytest.mark.performance
def test_workflow_initialization_performance(temp_workspace):
    """Test workflow initialization is fast"""
    start = time.time()
    
    workflow = SoftwareDevWorkflow(
        project_id="perf_test",
        workspace_path=temp_workspace,
        vision="Test vision"
    )
    
    elapsed = time.time() - start
    assert elapsed < 1.0  # Should initialize in under 1 second
    assert workflow is not None


@pytest.mark.performance
def test_state_machine_transition_performance(temp_workspace):
    """Test state machine transitions are fast"""
    from llamaindex_crew.orchestrator.state_machine import (
        ProjectStateMachine, ProjectState, TransitionContext
    )
    
    state_machine = ProjectStateMachine(temp_workspace, "perf_test")
    
    start = time.time()
    for _ in range(100):
        state_machine.transition(ProjectState.PRODUCT_OWNER, TransitionContext(phase="test", data={}))
        state_machine.transition(ProjectState.META)
    
    elapsed = time.time() - start
    assert elapsed < 1.0  # 100 transitions should be under 1 second


@pytest.mark.performance
def test_task_manager_operations_performance(temp_workspace):
    """Test task manager operations are fast"""
    from llamaindex_crew.orchestrator.task_manager import (
        TaskManager, TaskDefinition, TaskStatus
    )
    
    db_path = temp_workspace / "perf_tasks.db"
    task_manager = TaskManager(db_path, "perf_test")
    
    # Register 100 tasks
    start = time.time()
    for i in range(100):
        task = TaskDefinition(
            task_id=f"task_{i}",
            phase="development",
            task_type="feature",
            description=f"Task {i}"
        )
        task_manager.register_task(task)
    
    elapsed = time.time() - start
    assert elapsed < 2.0  # 100 registrations should be under 2 seconds
    
    # Query performance
    start = time.time()
    for i in range(100):
        task_manager.get_task_status(f"task_{i}")
    
    elapsed = time.time() - start
    assert elapsed < 1.0  # 100 queries should be under 1 second
