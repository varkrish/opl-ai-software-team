"""
Prompt loader utility - preserved from original implementation
"""
import os
from pathlib import Path
from typing import Optional

def load_prompt(relative_path: str, fallback: Optional[str] = None) -> str:
    """
    Load a prompt from the prompts directory
    
    Args:
        relative_path: Relative path from prompts/ directory (e.g., 'dev_crew/implement_feature.txt')
        fallback: Fallback text if file not found
        
    Returns:
        Prompt text content
    """
    # Get the prompts directory
    # Look for prompts in the original location first
    current_file = Path(__file__)
    prompts_dir = current_file.parent.parent.parent / "ai_software_dev_crew" / "prompts"
    
    # If not found, try relative to llamaindex_crew
    if not prompts_dir.exists():
        prompts_dir = current_file.parent.parent.parent / "llamaindex_crew" / "prompts"
    
    # If still not found, try absolute path from workspace
    if not prompts_dir.exists():
        workspace_path = os.getenv("WORKSPACE_PATH", "./workspace")
        prompts_dir = Path(workspace_path).parent / "src" / "ai_software_dev_crew" / "prompts"
    
    prompt_file = prompts_dir / relative_path
    
    if prompt_file.exists():
        try:
            with open(prompt_file, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            if fallback:
                return fallback
            raise FileNotFoundError(f"Could not read prompt file {prompt_file}: {e}")
    else:
        if fallback:
            return fallback
        raise FileNotFoundError(f"Prompt file not found: {prompt_file}")
