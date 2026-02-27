import pytest
import tempfile
import shutil
import json
from pathlib import Path
import sys

# Ensure we can import from src
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow
from llamaindex_crew.orchestrator.task_manager import TaskDefinition, TaskStatus

@pytest.fixture
def temp_workspace():
    """Create temporary workspace"""
    workspace = tempfile.mkdtemp()
    yield Path(workspace)
    shutil.rmtree(workspace)

@pytest.mark.integration
def test_workflow_self_healing_validation(temp_workspace):
    """Test that workflow validation uses TaskManager self-healing"""
    workflow = SoftwareDevWorkflow(
        project_id="test_healing_project",
        workspace_path=temp_workspace,
        vision="Test self-healing"
    )
    
    # Register tasks manually (simulating tech_stack extraction)
    task1 = TaskDefinition(
        task_id="file_readme_md",
        phase="development",
        task_type="file_creation",
        description="Create README.md",
        metadata={"file_path": "README.md"}
    )
    task2 = TaskDefinition(
        task_id="file_src_main_py",
        phase="development",
        task_type="file_creation",
        description="Create main.py",
        metadata={"file_path": "src/main.py"}
    )
    workflow.task_manager.register_task(task1)
    workflow.task_manager.register_task(task2)
    
    # Verify they are registered but not completed
    assert workflow.task_manager.get_task_status("file_readme_md") == TaskStatus.REGISTERED
    assert workflow.task_manager.get_task_status("file_src_main_py") == TaskStatus.REGISTERED
    
    # Physically create the files (simulating agent forgot to output markers or path mismatch)
    (temp_workspace / "README.md").write_text("# DONE")
    (temp_workspace / "backend" / "src").mkdir(parents=True, exist_ok=True)
    (temp_workspace / "backend" / "src" / "main.py").write_text("print('hello')")
    
    # Run the validation part of the workflow
    # We call the method that we just updated to use self-healing
    validation_results = workflow.task_manager.validate_all_tasks_completed(workflow.workspace_path)
    
    # Assertions
    assert validation_results["valid"] is True
    assert workflow.task_manager.get_task_status("file_readme_md") == TaskStatus.COMPLETED
    assert workflow.task_manager.get_task_status("file_src_main_py") == TaskStatus.COMPLETED
    
    # Check that it actually matched the moved file
    history = workflow.task_manager.get_task_history("file_src_main_py")
    completion_entry = next(e for e in history if e["event_type"] == "completed")
    assert "File found on disk at backend/src/main.py" in completion_entry["event_data"]["error"]

if __name__ == "__main__":
    pytest.main([__file__])
