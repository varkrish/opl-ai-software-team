"""
Unit tests for TaskManager
"""
import pytest
import tempfile
import shutil
from pathlib import Path
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from llamaindex_crew.orchestrator.task_manager import TaskManager, TaskDefinition, TaskStatus


@pytest.fixture
def temp_workspace():
    """Create temporary workspace"""
    workspace = tempfile.mkdtemp()
    yield Path(workspace)
    shutil.rmtree(workspace)


@pytest.fixture
def task_manager(temp_workspace):
    """Create TaskManager instance"""
    db_path = temp_workspace / "test_tasks.db"
    return TaskManager(db_path, "test_project")


def test_task_registration(task_manager):
    """Test task registration"""
    task = TaskDefinition(
        task_id="test_task_1",
        phase="development",
        task_type="feature",
        description="Test feature",
        source="test.feature"
    )
    
    task_manager.register_task(task)
    
    status = task_manager.get_task_status("test_task_1")
    assert status == TaskStatus.REGISTERED


def test_task_status_updates(task_manager):
    """Test task status updates"""
    task = TaskDefinition(
        task_id="test_task_2",
        phase="development",
        task_type="file_creation",
        description="Create file",
        source="tech_stack.md"
    )
    
    task_manager.register_task(task)
    task_manager.mark_task_created("test_task_2")
    assert task_manager.get_task_status("test_task_2") == TaskStatus.CREATED
    
    task_manager.mark_task_started("test_task_2")
    assert task_manager.get_task_status("test_task_2") == TaskStatus.IN_PROGRESS
    
    task_manager.mark_task_executed("test_task_2", TaskStatus.COMPLETED)
    assert task_manager.get_task_status("test_task_2") == TaskStatus.COMPLETED


def test_task_validation(task_manager):
    """Test task validation"""
    # Register some tasks
    task1 = TaskDefinition(
        task_id="task1",
        phase="development",
        task_type="feature",
        description="Feature 1",
        required=True
    )
    task2 = TaskDefinition(
        task_id="task2",
        phase="development",
        task_type="file_creation",
        description="File 1",
        required=True
    )
    
    task_manager.register_task(task1)
    task_manager.register_task(task2)
    
    # Mark one as created, leave one registered
    task_manager.mark_task_created("task1")
    
    # Validation should show task2 as missing
    validation = task_manager.validate_all_tasks_created()
    assert not validation['valid']
    assert "task2" in validation['missing_tasks']


def test_task_history(task_manager):
    """Test task execution history"""
    task = TaskDefinition(
        task_id="test_task_3",
        phase="development",
        task_type="feature",
        description="Test feature"
    )
    
    task_manager.register_task(task)
    task_manager.mark_task_created("test_task_3")
    task_manager.mark_task_started("test_task_3")
    task_manager.mark_task_executed("test_task_3", TaskStatus.COMPLETED)
    
    history = task_manager.get_task_history("test_task_3")
    assert len(history) >= 3
    assert history[0]['event_type'] == 'registered'
    assert history[-1]['event_type'] == 'completed'
