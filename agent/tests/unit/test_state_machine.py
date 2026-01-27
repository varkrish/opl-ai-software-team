"""
Unit tests for State Machine
"""
import pytest
import tempfile
import shutil
from pathlib import Path
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from llamaindex_crew.orchestrator.state_machine import (
    ProjectStateMachine, ProjectState, TransitionContext
)


@pytest.fixture
def temp_workspace():
    """Create temporary workspace"""
    workspace = tempfile.mkdtemp()
    yield Path(workspace)
    shutil.rmtree(workspace)


@pytest.fixture
def state_machine(temp_workspace):
    """Create state machine instance"""
    return ProjectStateMachine(temp_workspace, "test_project")


def test_initial_state(state_machine):
    """Test initial state is META"""
    assert state_machine.get_current_state() == ProjectState.META


def test_valid_transition(state_machine):
    """Test valid state transition"""
    state_machine.transition(
        ProjectState.PRODUCT_OWNER,
        TransitionContext(phase="product_owner", data={})
    )
    assert state_machine.get_current_state() == ProjectState.PRODUCT_OWNER


def test_invalid_transition(state_machine):
    """Test invalid state transition raises error"""
    with pytest.raises(ValueError):
        state_machine.transition(ProjectState.COMPLETED)


def test_rollback(state_machine):
    """Test state rollback"""
    # Move forward
    state_machine.transition(ProjectState.PRODUCT_OWNER)
    state_machine.transition(ProjectState.DESIGNER)
    
    # Rollback
    state_machine.rollback_to(ProjectState.PRODUCT_OWNER)
    assert state_machine.get_current_state() == ProjectState.PRODUCT_OWNER


def test_state_history(state_machine):
    """Test state transition history"""
    state_machine.transition(ProjectState.PRODUCT_OWNER)
    state_machine.transition(ProjectState.DESIGNER)
    
    history = state_machine.get_state_history()
    assert len(history) == 2
    assert history[0]['to_state'] == 'product_owner'
    assert history[1]['to_state'] == 'designer'
