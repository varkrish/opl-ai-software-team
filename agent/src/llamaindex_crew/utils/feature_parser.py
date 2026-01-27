"""
Feature parser to extract features from requirements and feature files
Preserved from original implementation
"""
import os
import re
from pathlib import Path
from typing import List, Dict


def parse_features_from_files(workspace_path: str = None) -> List[Dict[str, str]]:
    """
    Parse features from feature files and requirements
    
    Args:
        workspace_path: Path to workspace directory
        
    Returns:
        List of feature dictionaries with name and description
    """
    if workspace_path is None:
        workspace_path = os.getenv("WORKSPACE_PATH", "./workspace")
    
    workspace = Path(workspace_path)
    features = []
    
    # Parse .feature files
    features_dir = workspace / "features"
    if features_dir.exists():
        for feature_file in features_dir.glob("*.feature"):
            feature_name = feature_file.stem  # e.g., "addition" from "addition.feature"
            
            # Read feature file to get description
            try:
                with open(feature_file, 'r') as f:
                    content = f.read()
                    
                # Extract feature description from Gherkin
                feature_match = re.search(r'Feature:\s*(.+?)(?:\n|$)', content, re.IGNORECASE)
                feature_desc = feature_match.group(1).strip() if feature_match else feature_name
                
                # Extract scenarios to understand what needs to be implemented
                scenarios = re.findall(r'Scenario:\s*(.+?)(?:\n|$)', content, re.IGNORECASE | re.MULTILINE)
                
                features.append({
                    'name': feature_name,
                    'description': feature_desc,
                    'file': str(feature_file),
                    'scenarios': scenarios
                })
            except Exception as e:
                # If can't read, use filename as feature name
                features.append({
                    'name': feature_name,
                    'description': feature_name,
                    'file': str(feature_file),
                    'scenarios': []
                })
    
    # If no feature files, try to extract from requirements.md
    if not features:
        requirements_file = workspace / "requirements.md"
        if requirements_file.exists():
            try:
                with open(requirements_file, 'r') as f:
                    content = f.read()
                
                # Look for user stories or feature mentions
                # Pattern: "User Story X: Addition" or "Feature: Addition"
                story_pattern = r'(?:User Story|Feature):\s*([^\n]+)'
                matches = re.findall(story_pattern, content, re.IGNORECASE)
                
                for match in matches:
                    feature_name = match.strip().split()[0].lower()  # First word
                    features.append({
                        'name': feature_name,
                        'description': match.strip(),
                        'file': str(requirements_file),
                        'scenarios': []
                    })
            except Exception as e:
                pass
    
    return features


def get_feature_list(workspace_path: str = None) -> List[str]:
    """
    Get simple list of feature names
    
    Args:
        workspace_path: Path to workspace directory
        
    Returns:
        List of feature names
    """
    features = parse_features_from_files(workspace_path)
    return [f['name'] for f in features]
