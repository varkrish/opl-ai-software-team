import json
import time
import logging
from pathlib import Path
from typing import Dict, Any, Optional
import os

logger = logging.getLogger(__name__)

class StateManager:
    """Manages the persistence of orchestrator state"""
    
    def __init__(self, workspace_path: Path = None):
        if workspace_path is None:
            workspace_path = Path(os.getenv("WORKSPACE_PATH", "./workspace"))
        self.workspace_path = workspace_path
        self.state_file = workspace_path / ".orchestrator_state.json"
        
    def save_state(self, phase: str, data: Dict[str, Any]):
        """Save orchestrator state for resumability"""
        try:
            state = {
                'phase': phase,
                'data': data,
                'timestamp': time.time()
            }
            with open(self.state_file, 'w') as f:
                json.dump(state, f, indent=2)
            logger.debug(f"💾 State saved: {phase}")
        except Exception as e:
            logger.warning(f"Could not save state: {e}")
            
    def load_state(self) -> Optional[Dict[str, Any]]:
        """Load orchestrator state"""
        if not self.state_file.exists():
            return None
        try:
            with open(self.state_file, 'r') as f:
                state = json.load(f)
                logger.info(f"📂 Resuming from phase: {state.get('phase')}")
                return state
        except Exception as e:
            logger.warning(f"Could not load state: {e}")
            return None
            
    def clear_state(self):
        """Clear saved state"""
        if self.state_file.exists():
            try:
                self.state_file.unlink()
                logger.debug("🗑️  State cleared")
            except Exception as e:
                logger.warning(f"Could not clear state: {e}")
