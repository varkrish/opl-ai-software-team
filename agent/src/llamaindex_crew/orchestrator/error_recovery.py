"""
Error Recovery Engine for LlamaIndex Workflows
Adapted from original ErrorRecoveryEngine for workflow-based execution
"""
import logging
from typing import Dict, Any, Optional
from .state_machine import (
    ProjectStateMachine,
    ProjectState,
    ErrorContext,
    ErrorRecoveryEngine as BaseErrorRecoveryEngine
)

logger = logging.getLogger(__name__)


class WorkflowErrorRecoveryEngine(BaseErrorRecoveryEngine):
    """Error recovery engine adapted for LlamaIndex workflows"""
    
    def __init__(self, state_machine: ProjectStateMachine):
        """
        Initialize workflow error recovery engine
        
        Args:
            state_machine: Project state machine instance
        """
        super().__init__(state_machine)
        self.workflow_errors: Dict[str, Any] = {}
    
    def handle_workflow_error(
        self,
        phase: str,
        error: Exception,
        retry_count: int = 0
    ) -> Dict[str, Any]:
        """
        Handle error during workflow execution
        
        Args:
            phase: Current workflow phase
            error: Exception that occurred
            retry_count: Number of retries attempted
        
        Returns:
            Recovery strategy dictionary
        """
        # Create error context
        error_context = ErrorContext(
            error_type=type(error).__name__,
            failed_agent=phase,
            error_message=str(error),
            failure_count=retry_count + 1,
            rollback_target=self._get_rollback_target_for_phase(phase),
            recovery_actions=self._suggest_recovery_actions(error, phase)
        )
        
        # Analyze error
        recovery_strategy = self.analyze_error(error_context)
        
        # Store error for tracking
        self.workflow_errors[phase] = {
            'error': str(error),
            'retry_count': retry_count,
            'strategy': recovery_strategy
        }
        
        return recovery_strategy
    
    def _get_rollback_target_for_phase(self, phase: str) -> ProjectState:
        """Get rollback target state for a given phase"""
        phase_map = {
            'meta': ProjectState.META,
            'product_owner': ProjectState.PRODUCT_OWNER,
            'designer': ProjectState.DESIGNER,
            'tech_architect': ProjectState.TECH_ARCHITECT,
            'development': ProjectState.DEVELOPMENT,
            'frontend': ProjectState.FRONTEND
        }
        
        current = ProjectState[phase.upper()] if hasattr(ProjectState, phase.upper()) else None
        if not current:
            # Try to find by value
            for state in ProjectState:
                if state.value == phase:
                    current = state
                    break
        
        if current == ProjectState.META:
            return ProjectState.META
        elif current == ProjectState.PRODUCT_OWNER:
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
    
    def _suggest_recovery_actions(self, error: Exception, phase: str) -> list:
        """Suggest recovery actions based on error type and phase"""
        actions = []
        
        error_str = str(error).lower()
        
        # Budget-related errors
        if 'budget' in error_str or 'cost' in error_str:
            actions.append("Check budget limits and adjust if needed")
            actions.append("Consider using a cheaper model")
        
        # File-related errors
        if 'file' in error_str or 'permission' in error_str:
            actions.append("Check file permissions and workspace path")
            actions.append("Ensure workspace directory exists")
        
        # LLM API errors
        if 'api' in error_str or 'key' in error_str or '401' in error_str or '403' in error_str:
            actions.append("Verify API keys are set correctly")
            actions.append("Check API quota and rate limits")
        
        # Timeout errors
        if 'timeout' in error_str or 'timed out' in error_str:
            actions.append("Increase timeout settings")
            actions.append("Simplify the task or break it into smaller parts")
        
        # Agent-specific recovery actions
        if phase == 'meta':
            actions.append("Re-analyze vision with more context")
        elif phase == 'product_owner':
            actions.append("Review vision and requirements more carefully")
        elif phase == 'designer':
            actions.append("Re-read user stories and design requirements")
        elif phase == 'tech_architect':
            actions.append("Review design spec and project constraints")
        elif phase == 'development':
            actions.append("Check tech stack and ensure all dependencies are available")
        elif phase == 'frontend':
            actions.append("Review design spec and ensure UI components match design")
        
        return actions if actions else ["Retry the operation", "Check logs for more details"]
    
    def should_retry(self, phase: str, retry_count: int, max_retries: int = 3) -> bool:
        """
        Determine if workflow should retry a phase
        
        Args:
            phase: Current phase
            retry_count: Number of retries attempted
            max_retries: Maximum number of retries allowed
        
        Returns:
            True if should retry, False otherwise
        """
        if retry_count >= max_retries:
            return False
        
        # Check if error suggests retry
        if phase in self.workflow_errors:
            strategy = self.workflow_errors[phase].get('strategy', {})
            return strategy.get('suggests_retry', True)
        
        return True
    
    def should_rollback(self, phase: str) -> bool:
        """
        Determine if workflow should rollback to previous phase
        
        Args:
            phase: Current phase
        
        Returns:
            True if should rollback, False otherwise
        """
        if phase in self.workflow_errors:
            strategy = self.workflow_errors[phase].get('strategy', {})
            return strategy.get('suggests_rollback', False)
        
        return False
    
    def get_rollback_target(self, phase: str) -> ProjectState:
        """
        Get the target state for rollback
        
        Args:
            phase: Current phase
        
        Returns:
            Target state for rollback
        """
        if phase in self.workflow_errors:
            strategy = self.workflow_errors[phase].get('strategy', {})
            rollback_target = strategy.get('rollback_target')
            if rollback_target:
                return rollback_target
        
        return self._get_rollback_target_for_phase(phase)
