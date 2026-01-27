"""
Database tests for TaskManager SQLite operations
"""
import pytest
import tempfile
import shutil
import sqlite3
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


def test_database_creation(temp_workspace):
    """Test database is created with correct schema"""
    db_path = temp_workspace / "test_tasks.db"
    task_manager = TaskManager(db_path, "test_project")
    
    assert db_path.exists()
    
    # Verify tables exist
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [row[0] for row in cursor.fetchall()]
    
    assert 'tasks' in tables
    assert 'task_dependencies' in tables
    assert 'task_execution_log' in tables
    
    conn.close()


def test_task_persistence(temp_workspace):
    """Test tasks persist across TaskManager instances"""
    db_path = temp_workspace / "test_tasks.db"
    
    # Create first instance and register task
    task_manager1 = TaskManager(db_path, "test_project")
    task = TaskDefinition(
        task_id="persistent_task",
        phase="development",
        task_type="feature",
        description="Persistent task"
    )
    task_manager1.register_task(task)
    
    # Create second instance and verify task exists
    task_manager2 = TaskManager(db_path, "test_project")
    status = task_manager2.get_task_status("persistent_task")
    assert status == TaskStatus.REGISTERED


def test_task_execution_log(temp_workspace):
    """Test task execution log is persisted"""
    db_path = temp_workspace / "test_tasks.db"
    task_manager = TaskManager(db_path, "test_project")
    
    task = TaskDefinition(
        task_id="logged_task",
        phase="development",
        task_type="feature",
        description="Logged task"
    )
    task_manager.register_task(task)
    task_manager.mark_task_created("logged_task")
    task_manager.mark_task_started("logged_task")
    
    # Verify log entries
    history = task_manager.get_task_history("logged_task")
    assert len(history) >= 3
    assert any(e['event_type'] == 'registered' for e in history)
    assert any(e['event_type'] == 'created' for e in history)
    assert any(e['event_type'] == 'started' for e in history)
