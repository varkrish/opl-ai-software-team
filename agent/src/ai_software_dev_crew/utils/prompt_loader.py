"""
Utility for loading prompts from files
"""
import os
from pathlib import Path
from typing import Optional
import logging

logger = logging.getLogger(__name__)


def load_prompt(filename: str, fallback: Optional[str] = None) -> str:
    """
    Load a prompt from the prompts directory
    
    Args:
        filename: Name of the prompt file (e.g., 'business_analyst_backstory.txt')
        fallback: Optional fallback text if file is not found
        
    Returns:
        Prompt content as string, or fallback if file not found
    """
    # Get the prompts directory (same level as crews, utils, etc.)
    base_path = Path(__file__).parent.parent / 'prompts'
    filepath = base_path / filename
    
    if filepath.exists():
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                logger.debug(f"Loaded prompt from {filepath}")
                return content
        except Exception as e:
            logger.warning(f"Error reading prompt file {filepath}: {e}")
            if fallback:
                return fallback
            raise
    else:
        logger.warning(f"Prompt file not found: {filepath}")
        if fallback:
            logger.info(f"Using fallback for {filename}")
            return fallback
        else:
            raise FileNotFoundError(f"Prompt file not found: {filepath} and no fallback provided")

