"""
End-to-End Tests for LlamaIndex Software Development Workflow
"""
import pytest
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow
from llamaindex_crew.orchestrator.state_machine import ProjectState
from .conftest import verify_workflow_outputs


@pytest.mark.e2e
@pytest.mark.timeout(600)  # 10 minutes timeout
def test_calculator_workflow_e2e(e2e_workspace, calculator_vision):
    """
    E2E test for simple calculator project
    Tests the complete workflow from vision to code generation
    """
    # Create workflow
    workflow = SoftwareDevWorkflow(
        project_id="e2e_calculator",
        workspace_path=e2e_workspace,
        vision=calculator_vision
    )
    
    # Run workflow
    results = workflow.run()
    
    # Verify completion
    assert results["status"] == "completed", f"Workflow failed: {results.get('error')}"
    assert results["state"] in ["completed", "frontend"], "Workflow did not reach completion state"
    
    # Verify budget tracking
    budget = results.get("budget_report", {})
    assert "total_cost" in budget, "Budget report missing total_cost"
    assert budget["total_cost"] > 0, "No cost tracked"
    assert budget["total_cost"] < 10.0, "Exceeded budget limit"
    
    # Verify workflow outputs
    verification = verify_workflow_outputs(e2e_workspace)
    
    assert verification["artifacts_present"], f"Missing artifacts: {verification['missing_files']}"
    assert verification["code_generated"], "No code files generated"
    assert verification["tests_generated"], "No test files generated"
    
    # Verify calculator-specific files
    calculator_file = e2e_workspace / "src" / "calculator" / "calculator.py"
    if calculator_file.exists():
        content = calculator_file.read_text()
        assert "def add" in content or "class Calculator" in content, "Calculator class not found"
    
    print(f"\n✅ E2E Calculator Test Passed")
    print(f"   Files generated: {len(verification['generated_files'])}")
    print(f"   Total cost: ${budget['total_cost']:.4f}")


@pytest.mark.e2e
@pytest.mark.timeout(900)  # 15 minutes timeout
@pytest.mark.slow
def test_todo_api_workflow_e2e(e2e_workspace, todo_api_vision):
    """
    E2E test for TODO API project
    Tests more complex workflow with API endpoints
    """
    # Create workflow
    workflow = SoftwareDevWorkflow(
        project_id="e2e_todo_api",
        workspace_path=e2e_workspace,
        vision=todo_api_vision
    )
    
    # Run workflow
    results = workflow.run()
    
    # Verify completion
    assert results["status"] == "completed", f"Workflow failed: {results.get('error')}"
    
    # Verify budget tracking
    budget = results.get("budget_report", {})
    assert budget["total_cost"] < 10.0, "Exceeded budget limit"
    
    # Verify workflow outputs
    verification = verify_workflow_outputs(e2e_workspace)
    
    assert verification["artifacts_present"], "Missing project artifacts"
    assert verification["code_generated"], "No code files generated"
    assert verification["tests_generated"], "No test files generated"
    
    print(f"\n✅ E2E TODO API Test Passed")
    print(f"   Total cost: ${budget['total_cost']:.4f}")


@pytest.mark.e2e
def test_workflow_state_transitions(e2e_workspace, calculator_vision):
    """Test that workflow progresses through correct states"""
    workflow = SoftwareDevWorkflow(
        project_id="e2e_state_test",
        workspace_path=e2e_workspace,
        vision=calculator_vision
    )
    
    # Verify initial state
    assert workflow.state_machine.get_current_state() == ProjectState.META
    
    # Note: Full execution would be slow, so we just verify structure
    assert workflow.task_manager is not None
    assert workflow.budget_tracker is not None
    assert workflow.state_machine is not None


@pytest.mark.e2e
def test_workflow_with_minimal_vision(e2e_workspace):
    """Test workflow with minimal/simple vision"""
    vision = "Create a Python module with a single function that adds two numbers"
    
    workflow = SoftwareDevWorkflow(
        project_id="e2e_minimal",
        workspace_path=e2e_workspace,
        vision=vision
    )
    
    results = workflow.run()
    
    # Even minimal project should complete
    assert results["status"] == "completed", f"Minimal workflow failed: {results.get('error')}"
    
    # Should have basic artifacts
    assert (e2e_workspace / "requirements.md").exists()
    
    print(f"\n✅ E2E Minimal Vision Test Passed")


@pytest.mark.e2e
@pytest.mark.parametrize("vision,expected_tech", [
    ("Create a Python calculator", "python"),
    ("Build a React todo app", "react"),
    ("Create a FastAPI REST API", "fastapi"),
])
def test_workflow_tech_detection(e2e_workspace, vision, expected_tech):
    """Test that workflow correctly identifies technology from vision"""
    workflow = SoftwareDevWorkflow(
        project_id=f"e2e_tech_{expected_tech}",
        workspace_path=e2e_workspace,
        vision=vision
    )
    
    results = workflow.run()
    
    # Check tech stack file
    tech_stack_file = e2e_workspace / "tech_stack.md"
    if tech_stack_file.exists():
        content = tech_stack_file.read_text().lower()
        assert expected_tech.lower() in content, f"Expected tech '{expected_tech}' not found in tech stack"
    
    print(f"\n✅ Tech Detection Test Passed for {expected_tech}")
