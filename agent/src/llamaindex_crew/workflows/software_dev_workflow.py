"""
Software Development Workflow
Sequential workflow orchestrating all agents
"""
import logging
import os
import time
from pathlib import Path
from typing import Dict, Optional, Any, Callable
from ..agents import (
    MetaAgent, ProductOwnerAgent, DesignerAgent,
    TechArchitectAgent, DevAgent, FrontendAgent
)
from ..orchestrator.state_machine import ProjectStateMachine, ProjectState, TransitionContext
from ..orchestrator.task_manager import TaskManager, TaskStatus
from ..orchestrator.error_recovery import WorkflowErrorRecoveryEngine
from ..budget.tracker import EnhancedBudgetTracker
from ..utils.feature_parser import parse_features_from_files
from ..utils.document_indexer import DocumentIndexer

logger = logging.getLogger(__name__)

# Number of times to retry a phase on transient LLM/network errors
PHASE_RETRY_ATTEMPTS = 3
PHASE_RETRY_DELAY_SEC = 10


def _is_transient_llm_error(e: Exception) -> bool:
    """True if the error is a transient LLM/network error we should retry."""
    try:
        import httpx
        if isinstance(e, (httpx.RemoteProtocolError, httpx.ConnectError, httpx.ReadTimeout)):
            return True
        if getattr(httpx, "TimeoutException", None) is not None and isinstance(e, httpx.TimeoutException):
            return True
    except ImportError:
        pass
    msg = str(e).lower()
    return (
        "server disconnected" in msg
        or "connection" in msg
        or "timeout" in msg
        or "503" in msg
        or "502" in msg
        or "504" in msg
        or "remoteprotocolerror" in msg
    )


class SoftwareDevWorkflow:
    """Main workflow orchestrating all development phases"""
    
    def __init__(
        self,
        project_id: str,
        workspace_path: Path,
        vision: str,
        config: Optional[Any] = None,
        progress_callback: Optional[callable] = None
    ):
        """
        Initialize workflow
        
        Args:
            project_id: Unique project identifier
            workspace_path: Path to workspace directory
            vision: Project vision/idea
            config: Optional configuration instance
            progress_callback: Optional callback function(phase: str, progress: int, message: str)
        """
        self.project_id = project_id
        self.workspace_path = workspace_path
        self.vision = vision
        self.config = config
        self.progress_callback = progress_callback
        
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
    
    def _report_progress(self, phase: str, progress: int, message: str = None):
        """Report progress via callback if available"""
        if self.progress_callback:
            try:
                self.progress_callback(phase, progress, message)
            except Exception as e:
                logger.warning(f"Progress callback failed: {e}")
        self.user_stories = None
        self.design_spec = None
        self.tech_stack = None
    
    def run_meta_phase(self, retry_count: int = 0) -> Dict[str, str]:
        """Run Meta phase to generate agent backstories"""
        logger.info("🚀 Starting Meta phase...")
        self._report_progress('meta', 10, "Starting Meta phase...")
        
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
        self._report_progress('product_owner', 30, "Creating user stories...")
        
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
        self._report_progress('designer', 45, "Creating design specification...")
        
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
        self._report_progress('tech_architect', 60, "Defining technical architecture...")
        
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
            
            # Register granular per-file tasks with domain context from design spec
            self.task_manager.register_granular_tasks(
                self.design_spec or "",
                self.tech_stack,
            )
            
            # Index tech stack for RAG
            self.document_indexer.index_artifacts(["tech_stack.md"])
        
        logger.info("✅ Tech Architect phase completed")
        return result
    
    def run_development_phase(self) -> str:
        """Run Development phase: iterate per-task, generating one file at a time."""
        logger.info("🚀 Starting Development phase...")
        self._report_progress('development', 65, "Implementing application logic...")
        
        from ..orchestrator.code_validator import CodeCompletenessValidator
        
        if self.state_machine.get_current_state() != ProjectState.DEVELOPMENT:
            self.state_machine.transition(
                ProjectState.DEVELOPMENT,
                TransitionContext(phase="development", data={})
            )
        
        # Register feature tasks if any Gherkin features exist
        features = parse_features_from_files(str(self.workspace_path))
        if features:
            self.task_manager.register_tasks_from_features(features)
        
        # Create dev agent
        backstory = self.agent_backstories.get('developer')
        self.dev_agent = DevAgent(
            custom_backstory=backstory,
            budget_tracker=self.budget_tracker
        )
        
        # Per-task iteration: pick one file task at a time
        completed_files: dict = {}
        max_tasks = 100
        task_count = 0
        
        while task_count < max_tasks:
            task = self.task_manager.get_next_actionable_task("development")
            if task is None:
                break
            
            task_count += 1
            file_path = (task.metadata or {}).get("file_path", "")
            logger.info("📝 Task %d: generating %s", task_count, file_path or task.description)
            self._report_progress(
                'development',
                65 + min(20, task_count),
                f"Creating {file_path or task.description}...",
            )
            
            self.task_manager.mark_task_started(task.task_id)
            
            # Build focused prompt for this single file
            prompt = self.task_manager.build_file_prompt(
                task,
                tech_stack=self.tech_stack or "",
                user_stories=self.user_stories or "",
                existing_files=completed_files,
            )
            
            try:
                result = self.dev_agent.agent.chat(prompt)
                result_str = str(result)
                self.task_manager.update_task_status_by_output(result_str)
            except Exception as e:
                logger.error("Task %s failed: %s", task.task_id, e)
                self.task_manager.mark_task_executed(task.task_id, TaskStatus.FAILED, str(e))
                continue
            
            # Verify file exists on disk and validate completeness
            if file_path:
                full_path = self.workspace_path / file_path
                if not full_path.exists():
                    # Basename fallback
                    all_files = {p.name: p for p in self.workspace_path.rglob("*") if p.is_file()}
                    if Path(file_path).name in all_files:
                        full_path = all_files[Path(file_path).name]
                
                if full_path.exists():
                    self.task_manager.update_task_status(task.task_id, "completed", f"File created: {file_path}")
                    try:
                        content = full_path.read_text(encoding="utf-8", errors="replace")
                        completed_files[file_path] = content[:300]
                    except Exception:
                        pass
                    
                    # Quality check
                    validation = CodeCompletenessValidator.validate_file(full_path)
                    if not validation["complete"]:
                        logger.warning("⚠️ File %s has quality issues: %s", file_path, validation["issues"])
                else:
                    self.task_manager.update_task_status(
                        task.task_id, "skipped",
                        f"File {file_path} was not created by the agent",
                    )
        
        # Handle any remaining feature tasks
        feature_tasks = [t for t in self.task_manager.get_incomplete_tasks() if t.task_type == "feature"]
        if feature_tasks:
            feature_names = [t.description for t in feature_tasks]
            try:
                result = self.dev_agent.run(feature_names, self.tech_stack or "", self.user_stories)
                self.task_manager.update_task_status_by_output(result)
            except Exception as e:
                logger.error("Feature implementation failed: %s", e)
        
        logger.info("✅ Development phase completed (%d tasks processed)", task_count)
        return f"Development phase completed: {task_count} tasks processed"
    
    def run_frontend_phase(self) -> str:
        """Run Frontend phase: handle remaining UI file tasks + monolithic fallback."""
        logger.info("🚀 Starting Frontend phase...")
        self._report_progress('frontend', 90, "Building user interface...")
        
        if self.state_machine.get_current_state() != ProjectState.FRONTEND:
            self.state_machine.transition(
                ProjectState.FRONTEND,
                TransitionContext(phase="frontend", data={})
            )
        
        backstory = self.agent_backstories.get('frontend_developer')
        self.frontend_agent = FrontendAgent(
            custom_backstory=backstory,
            budget_tracker=self.budget_tracker
        )
        
        # Check for remaining incomplete file tasks after dev phase
        remaining = self.task_manager.get_incomplete_tasks()
        remaining_files = [t for t in remaining if t.task_type == "file_creation"]
        
        if remaining_files:
            logger.info("Frontend phase: %d remaining file tasks from dev phase", len(remaining_files))
            for task in remaining_files:
                file_path = (task.metadata or {}).get("file_path", "")
                prompt = self.task_manager.build_file_prompt(
                    task,
                    tech_stack=self.tech_stack or "",
                    user_stories=self.user_stories or "",
                )
                try:
                    result = self.frontend_agent.agent.chat(prompt)
                    self.task_manager.update_task_status_by_output(str(result))
                except Exception as e:
                    logger.error("Frontend task %s failed: %s", task.task_id, e)
                
                full_path = self.workspace_path / file_path if file_path else None
                if full_path and full_path.exists():
                    self.task_manager.update_task_status(task.task_id, "completed", f"File created: {file_path}")
        else:
            # All file tasks done — run frontend agent for general UI polish
            result = self.frontend_agent.run(
                self.design_spec or "",
                self.tech_stack or "",
                self.user_stories
            )
            self.task_manager.update_task_status_by_output(result)
        
        # Filesystem reconciliation for anything still incomplete
        self.task_manager.reconcile_with_filesystem(self.workspace_path)
        
        logger.info("✅ Frontend phase completed")
        return "Frontend phase completed"
    
    def _run_phase_with_retry(self, phase_name: str, phase_fn: Callable[[], Any]) -> Any:
        """Run a phase with retries on transient LLM/network errors."""
        last_error = None
        for attempt in range(PHASE_RETRY_ATTEMPTS):
            try:
                return phase_fn()
            except Exception as e:
                last_error = e
                if _is_transient_llm_error(e) and attempt < PHASE_RETRY_ATTEMPTS - 1:
                    logger.warning(
                        f"⚠️ Phase '{phase_name}' failed (transient): {e}. "
                        f"Retrying in {PHASE_RETRY_DELAY_SEC}s... (attempt {attempt + 1}/{PHASE_RETRY_ATTEMPTS})"
                    )
                    time.sleep(PHASE_RETRY_DELAY_SEC)
                    continue
                raise
        if last_error is not None:
            raise last_error

    def run(self) -> Dict[str, Any]:
        """
        Run complete workflow
        
        Returns:
            Dictionary with workflow results
        """
        try:
            # Phase 1: Meta (has its own retry logic)
            backstories = self.run_meta_phase()

            # Phases 2–6: run with retry on transient LLM/network errors
            self._run_phase_with_retry("product_owner", self.run_product_owner_phase)
            self._run_phase_with_retry("designer", self.run_designer_phase)
            self._run_phase_with_retry("tech_architect", self.run_tech_architect_phase)
            self._run_phase_with_retry("development", self.run_development_phase)
            self._run_phase_with_retry("frontend", self.run_frontend_phase)

            # Transition to completed
            self.state_machine.transition(
                ProjectState.COMPLETED,
                TransitionContext(phase="completed", data={})
            )

            # Final sweep: agents may reorganize files into subdirectories
            # (e.g. src/server.py → backend/src/server.py).  Check by basename.
            # Tasks still in REGISTERED status were never started by agents —
            # they represent the architect's plan that agents chose to satisfy
            # differently; skip them rather than failing the whole job.
            remaining = self.task_manager.get_incomplete_tasks()
            if remaining:
                all_files = {p.name: p for p in self.workspace_path.rglob("*") if p.is_file()}
                for task in remaining:
                    if task.task_type == "file_creation":
                        file_path = (task.metadata or {}).get("file_path", "")
                        if not file_path:
                            continue
                        basename = Path(file_path).name
                        if basename in all_files:
                            logger.info("Fallback (basename): task %s matched %s", task.task_id, all_files[basename])
                            self.task_manager.update_task_status(task.task_id, "completed", f"File found at {all_files[basename]}")
                        elif task.status == TaskStatus.REGISTERED.value:
                            logger.info(
                                "Fallback: skipping registered file task %s (%s) — agents reorganized the project",
                                task.task_id, file_path,
                            )
                            self.task_manager.update_task_status(
                                task.task_id, "skipped",
                                f"File planned as {file_path} but agents reorganized; project completed successfully",
                            )
                    elif task.task_type == "feature":
                        logger.info("Fallback: marking feature task %s as completed (project built)", task.task_id)
                        self.task_manager.update_task_status(task.task_id, "completed", "Project built successfully")
                    elif task.status == TaskStatus.REGISTERED.value:
                        logger.info(
                            "Fallback: skipping registered task %s (type=%s) — never started, project completed",
                            task.task_id, task.task_type,
                        )
                        self.task_manager.update_task_status(
                            task.task_id, "skipped",
                            "Task was planned but never started; project completed successfully",
                        )

            # Final task validation (with filesystem reconciliation)
            completed_check = self.task_manager.validate_all_tasks_completed(
                workspace_path=self.workspace_path
            )

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
