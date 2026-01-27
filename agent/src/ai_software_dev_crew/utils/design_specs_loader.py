"""
Design specifications loader
Reads design specification files and makes them available to crews
Supports both local files and URLs
"""
import os
from pathlib import Path
from typing import Dict, List, Optional
from .url_fetcher import fetch_urls, is_valid_url


def load_design_specs(specs_path: str = None, urls: List[str] = None) -> Dict[str, str]:
    """
    Load design specification files from a directory and/or URLs
    
    Args:
        specs_path: Path to directory containing design specification files
                   If None, looks for 'design-specs' directory in workspace
        urls: Optional list of URLs to fetch design specifications from
        
    Returns:
        Dictionary mapping file names/URLs to their contents
    """
    specs = {}
    
    # Load from local directory
    if specs_path is None:
        workspace_path = os.getenv("WORKSPACE_PATH", "./workspace")
        specs_path = os.path.join(workspace_path, "design-specs")
    
    specs_dir = Path(specs_path).expanduser().resolve()
    
    if specs_dir.exists() and specs_dir.is_dir():
        # Supported file extensions
        supported_extensions = {'.md', '.txt', '.yaml', '.yml', '.json', '.py', '.js', '.ts', '.html', '.css'}
        
        # Read all files in the specs directory
        for file_path in specs_dir.rglob('*'):
            if file_path.is_file():
                # Check if file extension is supported
                if file_path.suffix.lower() in supported_extensions or file_path.suffix == '':
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            content = f.read()
                        
                        # Use relative path from specs_dir as key
                        relative_path = file_path.relative_to(specs_dir)
                        specs[str(relative_path)] = content
                    except Exception as e:
                        # Skip files that can't be read
                        continue
    
    # Fetch from URLs
    if urls:
        url_specs = fetch_urls(urls)
        # Add URL content to specs, using URL as key
        for url, content in url_specs.items():
            if content:
                # Use filename from URL or URL itself as key
                specs[f"url:{url}"] = content
    
    return specs


def format_specs_for_prompt(specs: Dict[str, str]) -> str:
    """
    Format design specifications for inclusion in prompts
    
    Args:
        specs: Dictionary of file names/URLs to contents
        
    Returns:
        Formatted string for prompts
    """
    if not specs:
        return ""
    
    formatted = "\n\n=== Design Specifications ===\n"
    formatted += "The following design specification files and URLs are provided:\n\n"
    
    for file_name, content in specs.items():
        if content:
            source_type = "URL" if file_name.startswith("url:") else "File"
            display_name = file_name.replace("url:", "") if file_name.startswith("url:") else file_name
            formatted += f"--- {source_type}: {display_name} ---\n"
            formatted += f"{content}\n\n"
    
    formatted += "=== End Design Specifications ===\n"
    
    return formatted


def get_specs_summary(specs: Dict[str, str]) -> str:
    """
    Get a summary of available design specifications
    
    Args:
        specs: Dictionary of file names/URLs to contents
        
    Returns:
        Summary string
    """
    if not specs:
        return "No design specifications provided."
    
    files = [f for f in specs.keys() if not f.startswith("url:")]
    urls = [f.replace("url:", "") for f in specs.keys() if f.startswith("url:")]
    
    summary = f"Design Specifications ({len(specs)} sources):\n"
    if files:
        summary += f"  Files ({len(files)}):\n"
        for file_name in sorted(files):
            summary += f"    - {file_name}\n"
    if urls:
        summary += f"  URLs ({len(urls)}):\n"
        for url in sorted(urls):
            summary += f"    - {url}\n"
    
    return summary

