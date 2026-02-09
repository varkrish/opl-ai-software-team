"""
Simple backend registry for pluggable agentic systems.
Minimal implementation: two backends (OPL, Aider), no config store.
"""
from typing import Dict, List, Optional, Callable, Any
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class Backend:
    """Simple backend protocol - each backend implements these methods."""
    
    name: str
    display_name: str
    
    def is_available(self) -> bool:
        """Check if this backend can run (e.g. CLI is installed)."""
        return True
    
    def run(self, job_id: str, vision: str, workspace_path: Path, 
            progress_callback: Callable[[str, int, str], None]) -> Dict[str, Any]:
        """
        Run the backend workflow.
        
        Args:
            job_id: Job ID
            vision: User's project vision
            workspace_path: Path to job workspace
            progress_callback: Function to report progress (phase, percent, message)
        
        Returns:
            Result dict with status, error, etc.
        """
        raise NotImplementedError


class BackendRegistry:
    """Simple registry for available backends."""
    
    def __init__(self):
        self._backends: Dict[str, Backend] = {}
    
    def register(self, backend: Backend):
        """Register a backend."""
        self._backends[backend.name] = backend
        logger.info(f"Registered backend: {backend.display_name} ({backend.name})")
    
    def list_backends(self) -> List[Dict[str, Any]]:
        """Return list of backends with metadata."""
        return [
            {
                "name": backend.name,
                "display_name": backend.display_name,
                "available": backend.is_available(),
            }
            for backend in self._backends.values()
        ]
    
    def get_backend(self, name: str) -> Optional[Backend]:
        """Get a backend by name."""
        return self._backends.get(name)


# Singleton registry
registry = BackendRegistry()


# Import and register backends
from .opl_crew import OPLCrewBackend
from .aider_backend import AiderBackend

registry.register(OPLCrewBackend())
registry.register(AiderBackend())


__all__ = ['Backend', 'BackendRegistry', 'registry']
