"""
Deterministic MTA report parser.

Converts MTA issues.json into DB-ready issue records without LLM calls.
Designed to fit in memory and handle reports up to ~10MB.
"""
import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

# Skip issues in these categories (not actionable)
SKIP_CATEGORIES = {"information"}

# Map MTA effort types to our DB values
EFFORT_MAP = {
    "Trivial": "low",
    "Info": "skip",  # Special sentinel (filtered out)
    "Architectural": "high",
}

# Map MTA issue categories to severity
CATEGORY_TO_SEVERITY = {
    "mandatory": "mandatory",
    "cloud-mandatory": "mandatory",
    "potential": "potential",
}


def is_mta_issues_json(report_path: Path) -> bool:
    """Check if a file is an MTA issues.json (array of {applicationId, issues})."""
    if not report_path.is_file():
        return False
    
    try:
        with open(report_path, encoding="utf-8") as f:
            data = json.load(f)
        
        # MTA issues.json is an array of objects with applicationId and issues
        if not isinstance(data, list) or len(data) == 0:
            return False
        
        # Check first element
        first = data[0]
        return isinstance(first, dict) and "applicationId" in first and "issues" in first
    
    except (json.JSONDecodeError, ValueError, KeyError, IOError):
        return False


_KNOWN_FILE_EXTENSIONS = {
    ".xml", ".properties", ".json", ".yaml", ".yml", ".txt", ".md",
    ".html", ".css", ".js", ".sql", ".sh", ".bat", ".cfg", ".conf",
    ".xsd", ".dtd", ".xsl", ".wsdl", ".jsp", ".jspx", ".tag",
    ".tld", ".war", ".jar", ".ear", ".gradle", ".kt", ".groovy",
}


def _resolve_file_path(file_name: str, files_json: Optional[Dict] = None) -> str:
    """Convert MTA fileName to actual file path.
    
    - If it looks like a path (contains /), pass through
    - If it has a known non-Java extension (pom.xml, etc.), return as-is
    - If it's a Java class name (com.foo.Bar), convert to src/main/java/com/foo/Bar.java
    """
    if "/" in file_name or "\\" in file_name:
        # Already a path
        return file_name.replace("\\", "/")
    
    # Check for known file extensions (e.g. pom.xml, application.properties)
    # These should NOT be treated as Java class names
    dot_idx = file_name.rfind(".")
    if dot_idx >= 0:
        ext = file_name[dot_idx:].lower()
        if ext in _KNOWN_FILE_EXTENSIONS:
            return file_name
    
    # Assume it's a Java class name (com.acmecorp.Foo)
    if "." in file_name:
        # Convert to path: com.acmecorp.Foo -> src/main/java/com/acmecorp/Foo.java
        return f"src/main/java/{file_name.replace('.', '/')}.java"
    
    # Fallback: return as-is
    return file_name


def parse_mta_issues_json(
    report_path: Path,
    files_json_path: Optional[Path] = None
) -> List[Dict[str, Any]]:
    """Parse MTA issues.json into a list of issue dicts ready for DB insertion.
    
    Returns list of dicts with keys: id, title, severity, effort, files, description, migration_hint.
    
    Deduplicates by ruleId (MTA duplicates issues across applicationIds).
    Skips 'information' category issues (not actionable).
    """
    with open(report_path, encoding="utf-8") as f:
        data = json.load(f)
    
    if not isinstance(data, list):
        raise ValueError("Expected MTA issues.json to be an array")
    
    # Optional: load files.json for path resolution
    # Normalize paths: MTA often uses "app/" prefix; workspace may have files at root
    files_map = {}
    if files_json_path and files_json_path.is_file():
        try:
            with open(files_json_path, encoding="utf-8") as fj:
                files_data = json.load(fj)
                for entry in files_data:
                    if "id" in entry and "fullPath" in entry:
                        full = entry["fullPath"].replace("\\", "/")
                        # Strip leading app/ so path matches workspace root
                        if full.startswith("app/"):
                            full = full[4:]
                        files_map[str(entry["id"])] = full
        except Exception as e:
            logger.warning("Could not load files.json: %s", e)
    
    # Collect all issues, keyed by ruleId for deduplication
    seen_rules: Dict[str, Dict[str, Any]] = {}
    
    for app in data:
        if not isinstance(app, dict) or "issues" not in app:
            continue
        
        issues_by_category = app["issues"]
        if not isinstance(issues_by_category, dict):
            continue
        
        for category, issue_list in issues_by_category.items():
            if category in SKIP_CATEGORIES:
                continue
            
            severity = CATEGORY_TO_SEVERITY.get(category, "optional")
            
            for issue in issue_list:
                if not isinstance(issue, dict):
                    continue
                
                rule_id = issue.get("ruleId")
                if not rule_id:
                    continue
                
                # If we already saw this rule, merge any NEW files into existing entry
                if rule_id in seen_rules:
                    existing = seen_rules[rule_id]
                    existing_files = set(existing["files"])
                    for af in issue.get("affectedFiles", []):
                        for file_entry in af.get("files", []):
                            file_id = str(file_entry.get("fileId", ""))
                            file_name = file_entry.get("fileName", "")
                            if file_id and file_id in files_map:
                                fpath = files_map[file_id]
                            elif file_name:
                                fpath = _resolve_file_path(file_name, files_map)
                            else:
                                continue
                            if fpath not in existing_files:
                                existing["files"].append(fpath)
                                existing_files.add(fpath)
                    continue
                
                # Extract fields
                name = issue.get("name", "Untitled Issue")
                effort_obj = issue.get("effort", {})
                effort_type = effort_obj.get("type", "Unknown")
                effort = EFFORT_MAP.get(effort_type, "medium")
                
                if effort == "skip":
                    # Info-level issues are not actionable
                    continue
                
                affected = issue.get("affectedFiles", [])
                if not affected:
                    # No files affected — skip
                    continue
                
                # Extract file paths and descriptions (deduplicate file paths)
                file_paths: List[str] = []
                file_paths_seen: set = set()
                descriptions = []
                
                for af in affected:
                    if not isinstance(af, dict):
                        continue
                    
                    desc = af.get("description", "")
                    if desc:
                        descriptions.append(desc)
                    
                    files_list = af.get("files", [])
                    for file_entry in files_list:
                        if not isinstance(file_entry, dict):
                            continue
                        
                        file_id = str(file_entry.get("fileId", ""))
                        file_name = file_entry.get("fileName", "")
                        
                        # Resolve to actual path
                        fpath = None
                        if file_id and file_id in files_map:
                            fpath = files_map[file_id]
                        elif file_name:
                            fpath = _resolve_file_path(file_name, files_map)
                        
                        if fpath and fpath not in file_paths_seen:
                            file_paths.append(fpath)
                            file_paths_seen.add(fpath)
                
                if not file_paths:
                    # No valid files — skip
                    continue
                
                # Build migration_hint from descriptions
                migration_hint = "\n\n".join(descriptions) if descriptions else name
                
                # Build description (summary of what needs to change)
                description = f"{name}. {descriptions[0] if descriptions else ''}"
                
                seen_rules[rule_id] = {
                    "id": rule_id,
                    "title": name,
                    "severity": severity,
                    "effort": effort,
                    "files": file_paths,
                    "description": description,
                    "migration_hint": migration_hint,
                }
    
    # Return deduplicated list
    return list(seen_rules.values())
