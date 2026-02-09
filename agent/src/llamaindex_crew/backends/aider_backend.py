"""
Aider Backend - CLI-based AI pair programmer.
"""
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, Callable
import logging
from . import Backend

logger = logging.getLogger(__name__)


class AiderBackend(Backend):
    """Aider - AI pair programming in the terminal."""
    
    name = "aider"
    display_name = "Aider"
    
    def is_available(self) -> bool:
        """Check if aider CLI is installed."""
        return shutil.which("aider") is not None
    
    def run(self, job_id: str, vision: str, workspace_path: Path,
            progress_callback: Callable[[str, int, str], None]) -> Dict[str, Any]:
        """
        Run aider as a subprocess.
        
        Invokes: aider --yes --no-git --message "<vision>"
        """
        logger.info(f"Aider starting job {job_id}")
        progress_callback("aider", 0, "Starting Aider...")
        
        if not self.is_available():
            error_msg = "Aider CLI not found. Install: pip install aider-chat"
            logger.error(error_msg)
            progress_callback("aider", 0, error_msg)
            return {"status": "error", "error": error_msg}
        
        try:
            # Build aider command
            cmd = [
                "aider",
                "--yes",          # Auto-confirm
                "--no-git",       # Don't use git
                "--message", vision,  # Initial message
            ]
            
            logger.info(f"Running: {' '.join(cmd)}")
            progress_callback("aider", 10, "Running Aider CLI...")
            
            # Run aider in the workspace directory
            process = subprocess.Popen(
                cmd,
                cwd=str(workspace_path),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            
            # Stream output
            output_lines = []
            for line in process.stdout:
                line = line.strip()
                if line:
                    output_lines.append(line)
                    logger.debug(f"Aider: {line}")
                    # Report progress
                    progress_callback("aider", 50, line[:200])  # Truncate long lines
            
            # Wait for completion
            process.wait()
            
            if process.returncode == 0:
                logger.info(f"Aider completed job {job_id}")
                progress_callback("aider", 100, "Aider completed")
                return {
                    "status": "success",
                    "output": "\n".join(output_lines[-20:])  # Last 20 lines
                }
            else:
                error_msg = f"Aider exited with code {process.returncode}"
                logger.error(error_msg)
                progress_callback("aider", 100, error_msg)
                return {
                    "status": "error",
                    "error": error_msg,
                    "output": "\n".join(output_lines[-20:])
                }
                
        except Exception as e:
            error_msg = f"Aider failed: {str(e)}"
            logger.error(error_msg)
            progress_callback("aider", 100, error_msg)
            return {"status": "error", "error": error_msg}
