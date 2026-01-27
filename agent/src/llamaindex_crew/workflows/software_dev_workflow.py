"""
Software Development Workflow
Sequential workflow orchestrating all agents
"""
import logging
import os
from pathlib import Path
from typing import Dict, Optional, Any
from ..agents import (
    MetaAgent, ProductOwnerAgent, DesignerAgent,
    TechArchitectAgent, DevAgent, FrontendAgent
)
from ..orchestrator.state_machine import ProjectStateMachine, ProjectState, TransitionContext
from ..orchestrator.task_manager import TaskManager
from ..orchestrator.error_recovery import WorkflowErrorRecoveryEngine
from ..budget.tracker import EnhancedBudgetTracker
from ..utils.feature_parser import parse_features_from_files
from ..utils.document_indexer import DocumentIndexer

logger = logging.getLogger(__name__)


class SoftwareDevWorkflow:
    """Main workflow orchestrating all development phases"""
    
    def __init__(
        self,
        project_id: str,
        workspace_path: Path,
        vision: str,
        config: Optional[Any] = None
    ):
        """
        Initialize workflow
        
        Args:
            project_id: Unique project identifier
            workspace_path: Path to workspace directory
            vision: Project vision/idea
            config: Optional configuration instance
        """
        self.project_id = project_id
        self.workspace_path = workspace_path
        self.vision = vision
        self.config = config
        
        # Initialize components
        self.state_machine = ProjectStateMachine(workspace_path, project_id)
        self.task_manager = TaskManager(
            workspace_path / f"tasks_{project_id}.db",
            project_id
        )
        self.budget_tracker = EnhancedBudgetTracker()
        self.document_indexer = DocumentIndexer(workspace_path, project_id)
        self.error_recovery = WorkflowErrorRecoveryEngine(self.state_machine)
        
        # Initialize agents (will be created with custom backstories after meta phase)
        self.meta_agent = None
        self.product_owner_agent = None
        self.designer_agent = None
        self.tech_architect_agent = None
        self.dev_agent = None
        self.frontend_agent = None
        
        # Store phase outputs
        self.project_context = None
        self.agent_backstories = {}
        self.user_stories = None
        self.design_spec = None
        self.tech_stack = None
    
    def run_meta_phase(self, retry_count: int = 0) -> Dict[str, str]:
        """Run Meta phase to generate agent backstories"""
        logger.info("🚀 Starting Meta phase...")
        
        try:
            # Only transition if not already in META state (initial state)
            if self.state_machine.get_current_state() != ProjectState.META:
                self.state_machine.transition(
                    ProjectState.META,
                    TransitionContext(phase="meta", data={"vision": self.vision})
                )
            
            # Create meta agent
            self.meta_agent = MetaAgent(budget_tracker=self.budget_tracker)
            
            # Run meta agent
            backstories = self.meta_agent.run(self.vision)
            self.agent_backstories = backstories
            
            # Extract project context from meta agent analysis
            self.project_context = self.meta_agent.analyze_vision(self.vision)
            
            # Transition to next phase only after successful completion
            self.state_machine.transition(
                ProjectState.PRODUCT_OWNER,
                TransitionContext(phase="meta_completed", data={"backstories": backstories})
            )
            
            logger.info("✅ Meta phase completed")
            return backstories
        except Exception as e:
            recovery = self.error_recovery.handle_workflow_error("meta", e, retry_count)
            if self.error_recovery.should_retry("meta", retry_count) and retry_count < 3:
                logger.warning(f"⚠️  Meta phase failed, retrying... ({retry_count + 1}/3)")
                # Rollback to META state for retry
                self.state_machine.rollback_to(ProjectState.META)
                return self.run_meta_phase(retry_count + 1)
            else:
                logger.error(f"❌ Meta phase failed after {retry_count + 1} attempts")
                raise
    
    def run_product_owner_phase(self) -> str:
        """Run Product Owner phase to create user stories"""
        logger.info("🚀 Starting Product Owner phase...")
        
        # Ensure state is correct (meta phase should have already transitioned us here)
        if self.state_machine.get_current_state() != ProjectState.PRODUCT_OWNER:
            self.state_machine.transition(
                ProjectState.PRODUCT_OWNER,
                TransitionContext(phase="product_owner", data={})
            )
        
        # Create product owner agent with custom backstory
        backstory = self.agent_backstories.get('product_owner')
        self.product_owner_agent = ProductOwnerAgent(
            custom_backstory=backstory,
            budget_tracker=self.budget_tracker,
            document_indexer=self.document_indexer
        )
        
        # Run product owner agent
        result = self.product_owner_agent.run(self.vision, self.project_context)
        
        # Read generated user stories
        user_stories_file = self.workspace_path / "user_stories.md"
        if user_stories_file.exists():
            with open(user_stories_file, 'r', encoding='utf-8') as f:
                self.user_stories = f.read()
            
            # Index user stories for RAG
            self.document_indexer.index_artifacts(["user_stories.md"])
        
        logger.info("✅ Product Owner phase completed")
        return result
    
    def run_designer_phase(self) -> str:
        """Run Designer phase to create design specification"""
        logger.info("🚀 Starting Designer phase...")
        
        # Ensure state is correct
        if self.state_machine.get_current_state() != ProjectState.DESIGNER:
            self.state_machine.transition(
                ProjectState.DESIGNER,
                TransitionContext(phase="designer", data={})
            )
        
        # Create designer agent with custom backstory
        backstory = self.agent_backstories.get('designer')
        self.designer_agent = DesignerAgent(
            custom_backstory=backstory,
            budget_tracker=self.budget_tracker
        )
        
        # Run designer agent
        result = self.designer_agent.run(
            self.user_stories or "",
            self.project_context
        )
        
        # Read generated design spec
        design_spec_file = self.workspace_path / "design_spec.md"
        if design_spec_file.exists():
            with open(design_spec_file, 'r', encoding='utf-8') as f:
                self.design_spec = f.read()
            
            # Index design spec for RAG
            self.document_indexer.index_artifacts(["design_spec.md"])
        
        logger.info("✅ Designer phase completed")
        return result
    
    def run_tech_architect_phase(self) -> str:
        """Run Tech Architect phase to define tech stack"""
        logger.info("🚀 Starting Tech Architect phase...")
        
        # Ensure state is correct
        if self.state_machine.get_current_state() != ProjectState.TECH_ARCHITECT:
            self.state_machine.transition(
                ProjectState.TECH_ARCHITECT,
                TransitionContext(phase="tech_architect", data={})
            )
        
        # Create tech architect agent with custom backstory
        backstory = self.agent_backstories.get('tech_architect')
        self.tech_architect_agent = TechArchitectAgent(
            custom_backstory=backstory,
            budget_tracker=self.budget_tracker
        )
        
        # Run tech architect agent
        result = self.tech_architect_agent.run(
            self.design_spec or "",
            self.vision,
            self.project_context
        )
        
        # Read generated tech stack
        tech_stack_file = self.workspace_path / "tech_stack.md"
        if tech_stack_file.exists():
            with open(tech_stack_file, 'r', encoding='utf-8') as f:
                self.tech_stack = f.read()
            
            # Register file creation tasks from tech stack
            self.task_manager.register_tasks_from_tech_stack(tech_stack_file)
            
            # Index tech stack for RAG
            self.document_indexer.index_artifacts(["tech_stack.md"])
        
        logger.info("✅ Tech Architect phase completed")
        return result
    
    def run_development_phase(self) -> str:
        """Run Development phase to implement features"""
        logger.info("🚀 Starting Development phase...")
        
        # Ensure state is correct
        if self.state_machine.get_current_state() != ProjectState.DEVELOPMENT:
            self.state_machine.transition(
                ProjectState.DEVELOPMENT,
                TransitionContext(phase="development", data={})
            )
        
        # Parse features
        features = parse_features_from_files(str(self.workspace_path))
        if features:
            self.task_manager.register_tasks_from_features(features)
        
        # Create dev agent with custom backstory
        backstory = self.agent_backstories.get('developer')
        self.dev_agent = DevAgent(
            custom_backstory=backstory,
            budget_tracker=self.budget_tracker
        )
        
        # Run dev agent
        feature_names = [f['name'] for f in features] if features else ["main"]
        result = self.dev_agent.run(
            feature_names,
            self.tech_stack or "",
            self.user_stories
        )
        
        # Update task status based on output
        self.task_manager.update_task_status_by_output(result)
        
        # Check if all files were actually created on disk (fallback)
        tech_stack_file = self.workspace_path / "tech_stack.md"
        if tech_stack_file.exists():
            tech_tasks = self.task_manager.get_incomplete_tasks()
            for task in tech_tasks:
                if task.task_type == "file_creation":
                    file_path = task.metadata.get("file_path")
                    if file_path:
                        full_path = self.workspace_path / file_path
                        if full_path.exists():
                            logger.info(f"🔍 Fallback: Found file on disk for task {task.task_id}: {file_path}")
                            self.task_manager.update_task_status(task.task_id, "completed", "File found on disk")

        # Validate tasks
        created_check = self.task_manager.validate_all_tasks_created()
        if not created_check['valid']:
            logger.warning(f"⚠️  Some tasks not created: {created_check['missing_tasks'][:5]}")
        
        logger.info("✅ Development phase completed")
        return result
    
    def run_frontend_phase(self) -> str:
        """Run Frontend phase to implement UI"""
        logger.info("🚀 Starting Frontend phase...")
        
        # Ensure state is correct
        if self.state_machine.get_current_state() != ProjectState.FRONTEND:
            self.state_machine.transition(
                ProjectState.FRONTEND,
                TransitionContext(phase="frontend", data={})
            )
        
        # Create frontend agent with custom backstory
        backstory = self.agent_backstories.get('frontend_developer')
        self.frontend_agent = FrontendAgent(
            custom_backstory=backstory,
            budget_tracker=self.budget_tracker
        )
        
        # Run frontend agent
        result = self.frontend_agent.run(
            self.design_spec or "",
            self.tech_stack or "",
            self.user_stories
        )
        
        # Update task status based on output
        self.task_manager.update_task_status_by_output(result)
        
        # Check if all files were actually created on disk (fallback)
        tech_tasks = self.task_manager.get_incomplete_tasks()
        for task in tech_tasks:
            if task.task_type == "file_creation":
                file_path = task.metadata.get("file_path")
                if file_path:
                    full_path = self.workspace_path / file_path
                    if full_path.exists():
                        logger.info(f"🔍 Fallback: Found file on disk for task {task.task_id}: {file_path}")
                        self.task_manager.update_task_status(task.task_id, "completed", "File found on disk")
        
        logger.info("✅ Frontend phase completed")
        return result
    
    def run(self) -> Dict[str, Any]:
        """
        Run complete workflow
        
        Returns:
            Dictionary with workflow results
        """
        try:
            # Phase 1: Meta
            backstories = self.run_meta_phase()
            
            # Phase 2: Product Owner
            self.run_product_owner_phase()
            
            # Phase 3: Designer
            self.run_designer_phase()
            
            # Phase 4: Tech Architect
            self.run_tech_architect_phase()
            
            # Phase 5: Development
            self.run_development_phase()
            
            # Phase 6: Frontend
            self.run_frontend_phase()
            
            # Transition to completed
            self.state_machine.transition(
                ProjectState.COMPLETED,
                TransitionContext(phase="completed", data={})
            )
            
            # Final task validation
            completed_check = self.task_manager.validate_all_tasks_completed()
            
            return {
                "status": "completed",
                "project_id": self.project_id,
                "budget_report": self.budget_tracker.get_report(self.project_id),
                "task_validation": completed_check,
                "state": self.state_machine.get_current_state().value
            }
        except Exception as e:
            logger.error(f"❌ Workflow failed: {e}")
            self.state_machine.transition(
                ProjectState.FAILED,
                TransitionContext(phase="failed", data={"error": str(e)})
            )
            raise
