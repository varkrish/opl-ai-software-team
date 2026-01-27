"""
Project State Machine for workflow orchestration with rollback capability
"""
import logging
from enum import Enum
from dataclasses import dataclass
from typing import List, Optional, Dict, Any
from pathlib import Path
import json
import time

logger = logging.getLogger(__name__)


class ProjectState(Enum):
    """Project workflow states"""
    META = "meta"
    PRODUCT_OWNER = "product_owner"
    DESIGNER = "designer"
    TECH_ARCHITECT = "tech_architect"
    DEVELOPMENT = "development"
    FRONTEND = "frontend"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLBACK = "rollback"


@dataclass
class ErrorContext:
    """Context for error handling"""
    error_type: str
    failed_agent: str
    error_message: str
    failure_count: int
    rollback_target: ProjectState
    recovery_actions: List[str]
    timestamp: float = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = time.time()


@dataclass
class TransitionContext:
    """Context for state transitions"""
    phase: str
    data: Dict[str, Any]
    timestamp: float = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = time.time()
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for serialization"""
        return {
            'phase': self.phase,
            'data': self.data,
            'timestamp': self.timestamp
        }


class ProjectStateMachine:
    """State machine for project workflow with rollback capability"""
    
    def __init__(self, workspace_path: Path, project_id: str):
        self.workspace_path = workspace_path
        self.project_id = project_id
        self.state_file = workspace_path / f"state_{project_id}.json"
        
        # Define valid state transitions
        self.transitions = {
            ProjectState.META: [ProjectState.PRODUCT_OWNER, ProjectState.FAILED],
            ProjectState.PRODUCT_OWNER: [
                ProjectState.DESIGNER,
                ProjectState.META,  # Rollback
                ProjectState.FAILED
            ],
            ProjectState.DESIGNER: [
                ProjectState.TECH_ARCHITECT,
                ProjectState.PRODUCT_OWNER,  # Rollback
                ProjectState.FAILED
            ],
            ProjectState.TECH_ARCHITECT: [
                ProjectState.DEVELOPMENT,
                ProjectState.DESIGNER,  # Rollback
                ProjectState.FAILED
            ],
            ProjectState.DEVELOPMENT: [
                ProjectState.FRONTEND,
                ProjectState.TECH_ARCHITECT,  # Rollback
                ProjectState.FAILED
            ],
            ProjectState.FRONTEND: [
                ProjectState.COMPLETED,
                ProjectState.DEVELOPMENT,  # Rollback
                ProjectState.FAILED
            ],
            ProjectState.ROLLBACK: [
                ProjectState.META,
                ProjectState.PRODUCT_OWNER,
                ProjectState.DESIGNER,
                ProjectState.TECH_ARCHITECT,
                ProjectState.DEVELOPMENT,
                ProjectState.FAILED
            ],
            ProjectState.FAILED: [],  # Terminal state
            ProjectState.COMPLETED: [ProjectState.FAILED]  # Terminal state (can be marked failed post-completion)
        }
        
        # Load current state
        self.current_state = self._load_state()
        self.state_history: List[Dict] = []
    
    def _load_state(self) -> ProjectState:
        """Load current state from file"""
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r') as f:
                    state_data = json.load(f)
                    return ProjectState(state_data.get('current_state', ProjectState.META.value))
            except Exception as e:
                logger.warning(f"Could not load state: {e}")
        
        return ProjectState.META
    
    def _save_state(self):
        """Save current state to file"""
        try:
            state_data = {
                'current_state': self.current_state.value,
                'project_id': self.project_id,
                'timestamp': time.time()
            }
            with open(self.state_file, 'w') as f:
                json.dump(state_data, f, indent=2)
        except Exception as e:
            logger.error(f"Could not save state: {e}")
    
    def can_transition(self, to_state: ProjectState) -> bool:
        """Check if state transition is valid"""
        valid_transitions = self.transitions.get(self.current_state, [])
        return to_state in valid_transitions
    
    def transition(self, to_state: ProjectState, context: Optional[TransitionContext] = None) -> bool:
        """Execute state transition with validation"""
        if not self.can_transition(to_state):
            raise ValueError(
                f"Cannot transition from {self.current_state.value} to {to_state.value}. "
                f"Valid transitions: {[s.value for s in self.transitions.get(self.current_state, [])]}"
            )
        
        # Record transition in history
        transition_record = {
            'from_state': self.current_state.value,
            'to_state': to_state.value,
            'timestamp': time.time(),
            'context': context.to_dict() if context else None
        }
        self.state_history.append(transition_record)
        
        # Update state
        old_state = self.current_state
        self.current_state = to_state
        
        # Save state
        self._save_state()
        
        logger.info(f"State transition: {old_state.value} → {to_state.value}")
        
        return True
    
    def force_transition(self, to_state: ProjectState, context: Optional[TransitionContext] = None) -> bool:
        """Forcefully set the state (used when normal transitions are blocked)"""
        old_state = self.current_state
        self.current_state = to_state
        
        transition_record = {
            'from_state': old_state.value,
            'to_state': to_state.value,
            'timestamp': time.time(),
            'context': context.to_dict() if context else {'type': 'force'}
        }
        self.state_history.append(transition_record)
        self._save_state()
        logger.warning(f"Forced state transition: {old_state.value} → {to_state.value}")
        return True
    
    def rollback_to(self, target_state: ProjectState) -> bool:
        """Rollback to a previous state"""
        # Check if target state is valid for rollback
        # Allow rollback to any previous state in the workflow
        valid_rollback_targets = [
            ProjectState.META,
            ProjectState.PRODUCT_OWNER,
            ProjectState.DESIGNER,
            ProjectState.TECH_ARCHITECT,
            ProjectState.DEVELOPMENT
        ]
        
        if target_state not in valid_rollback_targets:
            logger.error(f"Invalid rollback target: {target_state.value}")
            return False
        
        # Direct transition to target state (rollback is a special case)
        # We bypass normal transition rules for rollback
        try:
            old_state = self.current_state
            self.current_state = target_state
            
            # Record rollback in history
            transition_record = {
                'from_state': old_state.value,
                'to_state': target_state.value,
                'timestamp': time.time(),
                'context': {'type': 'rollback'}
            }
            self.state_history.append(transition_record)
            
            # Save state
            self._save_state()
            
            logger.info(f"Rolled back: {old_state.value} → {target_state.value}")
            return True
        except Exception as e:
            logger.error(f"Rollback failed: {e}")
            return False
    
    def get_current_state(self) -> ProjectState:
        """Get current state"""
        return self.current_state
    
    def get_state_history(self) -> List[Dict]:
        """Get state transition history"""
        return self.state_history.copy()
    
    def is_terminal_state(self) -> bool:
        """Check if current state is terminal"""
        return self.current_state in [ProjectState.COMPLETED, ProjectState.FAILED]
    
    def reset(self):
        """Reset state machine to initial state"""
        self.current_state = ProjectState.META
        self.state_history = []
        self._save_state()
        logger.info("State machine reset to initial state")


class ErrorRecoveryEngine:
    """Engine for handling errors and determining recovery strategies"""
    
    def __init__(self, state_machine: ProjectStateMachine):
        self.state_machine = state_machine
        self.error_history: List[ErrorContext] = []
    
    def analyze_error(self, error: ErrorContext) -> Dict[str, Any]:
        """Analyze error and suggest recovery strategy"""
        self.error_history.append(error)
        
        # Get similar errors
        similar_errors = self._get_similar_errors(error.error_type)
        
        # Determine recovery strategy
        if error.failure_count > 3:
            return {
                'suggests_rollback': True,
                'suggests_reassignment': False,
                'suggests_retry': False,
                'rollback_target': self._determine_rollback_target(error),
                'recovery_plan': self._build_recovery_plan(error, 'rollback'),
                'reason': 'Persistent failure - rollback recommended'
            }
        
        if error.failure_count > 1:
            return {
                'suggests_rollback': False,
                'suggests_reassignment': True,
                'suggests_retry': True,
                'alternative_agent': self._suggest_alternative_agent(error),
                'recovery_plan': self._build_recovery_plan(error, 'retry'),
                'reason': 'Multiple failures - retry with modifications'
            }
        
        # First failure - just retry
        return {
            'suggests_rollback': False,
            'suggests_reassignment': False,
            'suggests_retry': True,
            'recovery_plan': self._build_recovery_plan(error, 'retry'),
            'reason': 'First failure - simple retry'
        }
    
    def _get_similar_errors(self, error_type: str) -> List[ErrorContext]:
        """Get similar errors from history"""
        return [e for e in self.error_history if e.error_type == error_type][-5:]  # Last 5 similar errors
    
    def _determine_rollback_target(self, error: ErrorContext) -> ProjectState:
        """Determine appropriate rollback target"""
        # Use specified rollback target if provided
        if error.rollback_target:
            return error.rollback_target
        
        # Default: rollback to previous phase
        current = self.state_machine.get_current_state()
        if current == ProjectState.PRODUCT_OWNER:
            return ProjectState.META
        elif current == ProjectState.DESIGNER:
            return ProjectState.PRODUCT_OWNER
        elif current == ProjectState.TECH_ARCHITECT:
            return ProjectState.DESIGNER
        elif current == ProjectState.DEVELOPMENT:
            return ProjectState.TECH_ARCHITECT
        elif current == ProjectState.FRONTEND:
            return ProjectState.DEVELOPMENT
        else:
            return ProjectState.META
    
    def _suggest_alternative_agent(self, error: ErrorContext) -> Optional[str]:
        """Suggest alternative agent for reassignment"""
        # Simple mapping - could be enhanced with ML
        agent_mapping = {
            'product_owner': 'business_analyst',
            'designer': 'architect',
            'tech_architect': 'senior_architect',
            'developer': 'senior_developer'
        }
        return agent_mapping.get(error.failed_agent)
    
    def _build_recovery_plan(self, error: ErrorContext, strategy: str) -> Dict[str, Any]:
        """Build recovery plan based on strategy"""
        if strategy == 'rollback':
            return {
                'action': 'rollback',
                'target_state': self._determine_rollback_target(error).value,
                'preserve_artifacts': self._get_artifacts_to_preserve(error),
                'modifications': error.recovery_actions
            }
        elif strategy == 'retry':
            return {
                'action': 'retry',
                'max_attempts': 3 - error.failure_count,
                'modifications': error.recovery_actions,
                'alternative_agent': self._suggest_alternative_agent(error)
            }
        else:
            return {
                'action': 'unknown',
                'modifications': error.recovery_actions
            }
    
    def _get_artifacts_to_preserve(self, error: ErrorContext) -> List[str]:
        """Determine which artifacts to preserve during rollback"""
        # Preserve all artifacts from previous phases
        current = self.state_machine.get_current_state()
        artifacts = []
        
        if current.value != 'meta':
            artifacts.append('meta_crew')
        if current.value not in ['meta', 'product_owner']:
            artifacts.append('user_stories')
        if current.value not in ['meta', 'product_owner', 'designer']:
            artifacts.append('design_spec')
        if current.value not in ['meta', 'product_owner', 'designer', 'tech_architect']:
            artifacts.append('tech_stack')
        
        return artifacts

