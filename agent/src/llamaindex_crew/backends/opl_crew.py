"""
OPL AI Team Backend - wraps the existing LlamaIndex crew workflow.
"""
from pathlib import Path
from typing import Dict, Any, Callable
import logging
from . import Backend

logger = logging.getLogger(__name__)


class OPLCrewBackend(Backend):
    """OPL AI Team - the existing multi-agent software dev crew."""
    
    name = "opl-ai-team"
    display_name = "OPL AI Team"
    
    def is_available(self) -> bool:
        """Always available (builtin)."""
        return True
    
    def run(self, job_id: str, vision: str, workspace_path: Path,
            progress_callback: Callable[[str, int, str], None]) -> Dict[str, Any]:
        """
        Run the OPL crew workflow.
        
        This delegates to the original run_job_async logic which handles
        all the workflow execution, progress tracking, and database updates.
        
        Note: progress_callback is not used - the workflow reports progress
        directly to the job database via the crew_studio.llamaindex_web_app module.
        """
        logger.info(f"OPL AI Team starting job {job_id}")
        
        try:
            # Import the original run_job_async function
            # This already handles all the workflow setup, progress tracking, etc.
            import sys
            # Add crew_studio to path if needed
            import os
            crew_studio_path = os.path.join(os.getcwd(), 'crew_studio')
            if crew_studio_path not in sys.path:
                sys.path.insert(0, crew_studio_path)
            
            # Import and call the ACTUAL implementation from run_job_async
            # We need to run the workflow execution part directly
            from src.llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow
            from src.llamaindex_crew.config import ConfigLoader
            
            # This needs to mimic what run_job_async does, but we can't import it
            # directly because it would create circular dependencies
            # So we'll just instantiate and run the workflow directly
            # The workflow handles its own progress tracking through the task database
            
            config = ConfigLoader.load()
            workflow = SoftwareDevWorkflow(
                project_id=job_id,
                workspace_path=workspace_path,
                vision=vision,
                config=config
            )
            
            # Run workflow (it manages its own state via task DB)
            result = workflow.run()
            
            logger.info(f"OPL AI Team completed job {job_id}")
            return {"status": "success", "result": result}
            
        except Exception as e:
            logger.error(f"OPL AI Team failed job {job_id}: {e}", exc_info=True)
            return {"status": "error", "error": str(e)}

