"""
Unit tests for ProjectStateMachine.

Covers:
- Every valid forward transition in the graph
- Backend-only / epic path: DEVELOPMENT → COMPLETED (no frontend phase)
- Frontend → COMPLETED path (no DevOps phase)
- Standard full path: META … DEVOPS → COMPLETED
- Rollback transitions at every stage
- Invalid transitions that must raise ValueError
- State history recording
- State persistence (save / load)
"""
import pytest
import tempfile
import shutil
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from llamaindex_crew.orchestrator.state_machine import (
    ProjectStateMachine,
    ProjectState,
    TransitionContext,
)


# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_ws():
    workspace = tempfile.mkdtemp()
    yield Path(workspace)
    shutil.rmtree(workspace)


def _make_sm(tmp_ws, project_id="proj"):
    return ProjectStateMachine(tmp_ws, project_id)


def _advance(sm, *states):
    """Advance through a list of states in order."""
    for s in states:
        sm.transition(s, TransitionContext(phase=s.value, data={}))


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------

class TestInitialState:
    def test_starts_at_meta(self, tmp_ws):
        sm = _make_sm(tmp_ws)
        assert sm.get_current_state() == ProjectState.META


# ---------------------------------------------------------------------------
# Full standard path: META → PO → DESIGNER → TA → DEV → FRONTEND → DEVOPS → COMPLETED
# ---------------------------------------------------------------------------

class TestStandardFullPath:
    def test_meta_to_product_owner(self, tmp_ws):
        sm = _make_sm(tmp_ws)
        sm.transition(ProjectState.PRODUCT_OWNER)
        assert sm.get_current_state() == ProjectState.PRODUCT_OWNER

    def test_product_owner_to_designer(self, tmp_ws):
        sm = _make_sm(tmp_ws)
        _advance(sm, ProjectState.PRODUCT_OWNER, ProjectState.DESIGNER)
        assert sm.get_current_state() == ProjectState.DESIGNER

    def test_designer_to_tech_architect(self, tmp_ws):
        sm = _make_sm(tmp_ws)
        _advance(sm, ProjectState.PRODUCT_OWNER, ProjectState.DESIGNER,
                 ProjectState.TECH_ARCHITECT)
        assert sm.get_current_state() == ProjectState.TECH_ARCHITECT

    def test_tech_architect_to_development(self, tmp_ws):
        sm = _make_sm(tmp_ws)
        _advance(sm, ProjectState.PRODUCT_OWNER, ProjectState.DESIGNER,
                 ProjectState.TECH_ARCHITECT, ProjectState.DEVELOPMENT)
        assert sm.get_current_state() == ProjectState.DEVELOPMENT

    def test_development_to_frontend(self, tmp_ws):
        sm = _make_sm(tmp_ws)
        _advance(sm, ProjectState.PRODUCT_OWNER, ProjectState.DESIGNER,
                 ProjectState.TECH_ARCHITECT, ProjectState.DEVELOPMENT,
                 ProjectState.FRONTEND)
        assert sm.get_current_state() == ProjectState.FRONTEND

    def test_frontend_to_devops(self, tmp_ws):
        sm = _make_sm(tmp_ws)
        _advance(sm, ProjectState.PRODUCT_OWNER, ProjectState.DESIGNER,
                 ProjectState.TECH_ARCHITECT, ProjectState.DEVELOPMENT,
                 ProjectState.FRONTEND, ProjectState.DEVOPS)
        assert sm.get_current_state() == ProjectState.DEVOPS

    def test_devops_to_completed(self, tmp_ws):
        sm = _make_sm(tmp_ws)
        _advance(sm, ProjectState.PRODUCT_OWNER, ProjectState.DESIGNER,
                 ProjectState.TECH_ARCHITECT, ProjectState.DEVELOPMENT,
                 ProjectState.FRONTEND, ProjectState.DEVOPS,
                 ProjectState.COMPLETED)
        assert sm.get_current_state() == ProjectState.COMPLETED


# ---------------------------------------------------------------------------
# Backend-only / Epic path: DEVELOPMENT → COMPLETED  (no frontend phase)
# This is the path used by Epic workflows and pure-backend projects.
# ---------------------------------------------------------------------------

class TestBackendOnlyEpicPath:
    def test_development_to_completed_directly(self, tmp_ws):
        """
        Epic/backend-only projects skip the frontend phase entirely.
        After the dev loop finishes all stories it must be able to transition
        DEVELOPMENT → COMPLETED without going through FRONTEND first.
        This was the regression caught in the E2E test.
        """
        sm = _make_sm(tmp_ws)
        _advance(sm, ProjectState.PRODUCT_OWNER, ProjectState.DESIGNER,
                 ProjectState.TECH_ARCHITECT, ProjectState.DEVELOPMENT)

        # Must NOT raise
        sm.transition(
            ProjectState.COMPLETED,
            TransitionContext(phase="completed", data={"epic": True}),
        )
        assert sm.get_current_state() == ProjectState.COMPLETED

    def test_development_to_completed_is_listed_in_valid_transitions(self, tmp_ws):
        """The transitions dict must include COMPLETED as a valid next state from DEVELOPMENT."""
        sm = _make_sm(tmp_ws)
        _advance(sm, ProjectState.PRODUCT_OWNER, ProjectState.DESIGNER,
                 ProjectState.TECH_ARCHITECT, ProjectState.DEVELOPMENT)
        assert sm.can_transition(ProjectState.COMPLETED), (
            "DEVELOPMENT → COMPLETED must be a valid transition for backend-only/epic workflows"
        )


# ---------------------------------------------------------------------------
# Frontend → COMPLETED (skip DevOps)
# ---------------------------------------------------------------------------

class TestFrontendSkipDevopsPath:
    def test_frontend_to_completed_directly(self, tmp_ws):
        sm = _make_sm(tmp_ws)
        _advance(sm, ProjectState.PRODUCT_OWNER, ProjectState.DESIGNER,
                 ProjectState.TECH_ARCHITECT, ProjectState.DEVELOPMENT,
                 ProjectState.FRONTEND)
        sm.transition(ProjectState.COMPLETED)
        assert sm.get_current_state() == ProjectState.COMPLETED


# ---------------------------------------------------------------------------
# FAILED transitions — every non-terminal state must be able to → FAILED
# ---------------------------------------------------------------------------

class TestFailedTransitions:
    @pytest.mark.parametrize("path,stuck_at", [
        ([], ProjectState.META),
        ([ProjectState.PRODUCT_OWNER], ProjectState.PRODUCT_OWNER),
        ([ProjectState.PRODUCT_OWNER, ProjectState.DESIGNER], ProjectState.DESIGNER),
        (
            [ProjectState.PRODUCT_OWNER, ProjectState.DESIGNER, ProjectState.TECH_ARCHITECT],
            ProjectState.TECH_ARCHITECT,
        ),
        (
            [ProjectState.PRODUCT_OWNER, ProjectState.DESIGNER,
             ProjectState.TECH_ARCHITECT, ProjectState.DEVELOPMENT],
            ProjectState.DEVELOPMENT,
        ),
        (
            [ProjectState.PRODUCT_OWNER, ProjectState.DESIGNER,
             ProjectState.TECH_ARCHITECT, ProjectState.DEVELOPMENT, ProjectState.FRONTEND],
            ProjectState.FRONTEND,
        ),
        (
            [ProjectState.PRODUCT_OWNER, ProjectState.DESIGNER,
             ProjectState.TECH_ARCHITECT, ProjectState.DEVELOPMENT,
             ProjectState.FRONTEND, ProjectState.DEVOPS],
            ProjectState.DEVOPS,
        ),
    ])
    def test_can_always_transition_to_failed(self, tmp_ws, path, stuck_at):
        sm = _make_sm(tmp_ws)
        _advance(sm, *path)
        sm.transition(ProjectState.FAILED)
        assert sm.get_current_state() == ProjectState.FAILED


# ---------------------------------------------------------------------------
# Invalid transitions that MUST raise ValueError
# ---------------------------------------------------------------------------

class TestInvalidTransitions:
    def test_meta_cannot_go_to_completed(self, tmp_ws):
        sm = _make_sm(tmp_ws)
        with pytest.raises(ValueError):
            sm.transition(ProjectState.COMPLETED)

    def test_meta_cannot_skip_to_development(self, tmp_ws):
        sm = _make_sm(tmp_ws)
        with pytest.raises(ValueError):
            sm.transition(ProjectState.DEVELOPMENT)

    def test_product_owner_cannot_go_to_development(self, tmp_ws):
        sm = _make_sm(tmp_ws)
        _advance(sm, ProjectState.PRODUCT_OWNER)
        with pytest.raises(ValueError):
            sm.transition(ProjectState.DEVELOPMENT)

    def test_designer_cannot_go_to_frontend(self, tmp_ws):
        sm = _make_sm(tmp_ws)
        _advance(sm, ProjectState.PRODUCT_OWNER, ProjectState.DESIGNER)
        with pytest.raises(ValueError):
            sm.transition(ProjectState.FRONTEND)

    def test_tech_architect_cannot_go_to_completed(self, tmp_ws):
        sm = _make_sm(tmp_ws)
        _advance(sm, ProjectState.PRODUCT_OWNER, ProjectState.DESIGNER,
                 ProjectState.TECH_ARCHITECT)
        with pytest.raises(ValueError):
            sm.transition(ProjectState.COMPLETED)

    def test_completed_is_terminal(self, tmp_ws):
        """Once COMPLETED, only FAILED is allowed (post-completion mark)."""
        sm = _make_sm(tmp_ws)
        _advance(sm, ProjectState.PRODUCT_OWNER, ProjectState.DESIGNER,
                 ProjectState.TECH_ARCHITECT, ProjectState.DEVELOPMENT,
                 ProjectState.COMPLETED)
        with pytest.raises(ValueError):
            sm.transition(ProjectState.META)

    def test_failed_is_terminal(self, tmp_ws):
        sm = _make_sm(tmp_ws)
        sm.transition(ProjectState.FAILED)
        with pytest.raises(ValueError):
            sm.transition(ProjectState.META)


# ---------------------------------------------------------------------------
# Rollback transitions
# ---------------------------------------------------------------------------

class TestRollbacks:
    def test_designer_rollback_to_product_owner(self, tmp_ws):
        sm = _make_sm(tmp_ws)
        _advance(sm, ProjectState.PRODUCT_OWNER, ProjectState.DESIGNER)
        sm.rollback_to(ProjectState.PRODUCT_OWNER)
        assert sm.get_current_state() == ProjectState.PRODUCT_OWNER

    def test_tech_architect_rollback_to_designer(self, tmp_ws):
        sm = _make_sm(tmp_ws)
        _advance(sm, ProjectState.PRODUCT_OWNER, ProjectState.DESIGNER,
                 ProjectState.TECH_ARCHITECT)
        sm.rollback_to(ProjectState.DESIGNER)
        assert sm.get_current_state() == ProjectState.DESIGNER

    def test_development_rollback_to_tech_architect(self, tmp_ws):
        sm = _make_sm(tmp_ws)
        _advance(sm, ProjectState.PRODUCT_OWNER, ProjectState.DESIGNER,
                 ProjectState.TECH_ARCHITECT, ProjectState.DEVELOPMENT)
        sm.rollback_to(ProjectState.TECH_ARCHITECT)
        assert sm.get_current_state() == ProjectState.TECH_ARCHITECT

    def test_frontend_rollback_to_development(self, tmp_ws):
        sm = _make_sm(tmp_ws)
        _advance(sm, ProjectState.PRODUCT_OWNER, ProjectState.DESIGNER,
                 ProjectState.TECH_ARCHITECT, ProjectState.DEVELOPMENT,
                 ProjectState.FRONTEND)
        sm.rollback_to(ProjectState.DEVELOPMENT)
        assert sm.get_current_state() == ProjectState.DEVELOPMENT


# ---------------------------------------------------------------------------
# State history
# ---------------------------------------------------------------------------

class TestStateHistory:
    def test_history_records_each_transition(self, tmp_ws):
        sm = _make_sm(tmp_ws)
        sm.transition(ProjectState.PRODUCT_OWNER)
        sm.transition(ProjectState.DESIGNER)
        history = sm.get_state_history()
        assert len(history) == 2
        assert history[0]["to_state"] == "product_owner"
        assert history[1]["to_state"] == "designer"

    def test_history_is_empty_initially(self, tmp_ws):
        sm = _make_sm(tmp_ws)
        assert sm.get_state_history() == []


# ---------------------------------------------------------------------------
# Persistence: state survives a reload from disk
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_state_persists_after_reload(self, tmp_ws):
        sm = _make_sm(tmp_ws, "persist-proj")
        _advance(sm, ProjectState.PRODUCT_OWNER, ProjectState.DESIGNER)

        sm2 = _make_sm(tmp_ws, "persist-proj")
        assert sm2.get_current_state() == ProjectState.DESIGNER

    def test_history_persists_after_reload(self, tmp_ws):
        sm = _make_sm(tmp_ws, "hist-proj")
        sm.transition(ProjectState.PRODUCT_OWNER)
        sm.transition(ProjectState.DESIGNER)

        sm2 = _make_sm(tmp_ws, "hist-proj")
        history = sm2.get_state_history()
        assert len(history) == 2
