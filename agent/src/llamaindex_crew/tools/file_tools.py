"""
File operation tools for AI agents
Migrated from CrewAI BaseTool to LlamaIndex FunctionTool
Supports explicit workspace_path for thread-safe use (e.g. refinement runner).
"""
import os
import logging
from pathlib import Path
from typing import Optional
from functools import partial
from llama_index.core.tools import FunctionTool
from ..utils.code_safety import CodeSafetyChecker

logger = logging.getLogger(__name__)


def _resolve_workspace(workspace_path: Optional[str] = None) -> Path:
    """Resolve workspace path: explicit path takes precedence over env (thread-safe)."""
    if workspace_path is not None:
        return Path(workspace_path)
    return Path(os.getenv("WORKSPACE_PATH", "./workspace"))


def file_writer(file_path: str, content: str, workspace_path: Optional[str] = None) -> str:
    """Write content to a file. Creates parent directories if needed. Use this tool to create or update any file in the workspace.
    
    Args:
        file_path: Path to the file to write (relative to workspace root). Example: 'index.html' or 'src/main.py'
        content: The content to write to the file.
        workspace_path: Optional workspace root (for thread-safe use). If not set, uses WORKSPACE_PATH env.
    
    Returns:
        Success or error message
    """
    try:
        workspace = _resolve_workspace(workspace_path)
        workspace.mkdir(parents=True, exist_ok=True)
        
        # Create full path
        full_path = workspace / file_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Safety checks
        safety_checker = CodeSafetyChecker()
        
        # Detect language from file extension
        language = 'python'  # default
        if file_path.endswith('.js') or file_path.endswith('.jsx'):
            language = 'javascript'
        elif file_path.endswith('.sh') or file_path.endswith('.bash'):
            language = 'bash'
        elif file_path.endswith('.py'):
            language = 'python'
        
        # Validate file write
        safety_result = safety_checker.check_file_write(file_path, content, language)
        
        if safety_result['blocked']:
            error_msg = f"❌ File write BLOCKED for {file_path} due to safety issues:\n"
            error_msg += "\n".join(f"  - {issue}" for issue in safety_result['issues'])
            logger.warning(error_msg)
            return error_msg
        
        if not safety_result['safe']:
            warning_msg = f"⚠️  Safety warnings for {file_path}:\n"
            warning_msg += "\n".join(f"  - {issue}" for issue in safety_result['issues'])
            logger.warning(warning_msg)
            # Continue with write but log warnings
        
        # Write file
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        success_msg = f"✅ Successfully wrote to {file_path} ({len(content)} characters)"
        if not safety_result['safe']:
            success_msg += "\n⚠️  Note: Some safety warnings were logged"
        
        return success_msg
    except Exception as e:
        logger.error(f"Error writing to {file_path}: {e}")
        return f"❌ Error writing to {file_path}: {str(e)}"


def file_line_replacer(
    file_path: str,
    start_line: int,
    end_line: int,
    new_content: str,
    workspace_path: Optional[str] = None
) -> str:
    """Replace a range of lines in a file. 
    
    Args:
        file_path: Relative path to the file.
        start_line: 1-indexed start line (inclusive).
        end_line: 1-indexed end line (inclusive).
        new_content: The new text to insert instead of the specified line range.
        workspace_path: Optional workspace path.
        
    Returns:
        Success or error message.
    """
    try:
        workspace = _resolve_workspace(workspace_path)
        full_path = workspace / file_path
        
        if not full_path.is_file():
            return f"❌ File not found: {file_path}"
        
        lines = full_path.read_text(encoding="utf-8").splitlines(keepends=True)
        total_lines = len(lines)
        
        if start_line < 1 or end_line > total_lines or start_line > end_line:
            return f"❌ Invalid line range: {start_line}-{end_line} (file has {total_lines} lines)"
        
        # Adjust for 0-based indexing
        # lines[start_line-1 : end_line] are the lines to be replaced
        new_lines = new_content.splitlines(keepends=True)
        # Ensure new_content ends with a newline if the original file did and we want to preserve structure
        if new_content and not new_content.endswith('\n'):
             new_lines[-1] = new_lines[-1] + '\n'
             
        lines[start_line - 1 : end_line] = new_lines
        
        full_path.write_text("".join(lines), encoding="utf-8")
        
        return f"✅ Successfully replaced lines {start_line}-{end_line} in {file_path}"
    except Exception as e:
        logger.error(f"Error replacing lines in {file_path}: {e}")
        return f"❌ Error replacing lines in {file_path}: {str(e)}"


def file_reader(file_path: str, workspace_path: Optional[str] = None) -> str:
    """Read content from a file in the workspace.
    
    Args:
        file_path: Path to the file to read (relative to workspace root). Example: 'requirements.md' or 'src/main.py'
        workspace_path: Optional workspace root (for thread-safe use). If not set, uses WORKSPACE_PATH env.
    
    Returns:
        File content or error message
    """
    try:
        workspace = _resolve_workspace(workspace_path)
        full_path = workspace / file_path
        
        if not full_path.exists():
            return f"❌ File not found: {file_path}"
        
        with open(full_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        return content
    except Exception as e:
        return f"❌ Error reading {file_path}: {str(e)}"


def file_lister(directory: str = ".", workspace_path: Optional[str] = None) -> str:
    """List all files in a directory recursively. Returns file paths relative to workspace and sizes.
    
    Args:
        directory: Directory path to list (relative to workspace root). Default is '.' (workspace root).
        workspace_path: Optional workspace root (for thread-safe use). If not set, uses WORKSPACE_PATH env.
    
    Returns:
        Recursive list of all files or error message
    """
    try:
        workspace = _resolve_workspace(workspace_path)
        full_path = workspace / directory
        
        if not full_path.exists():
            return f"❌ Directory not found: {directory}"
        
        if not full_path.is_dir():
            return f"❌ Not a directory: {directory}"
        
        # Skip hidden dirs, __pycache__, node_modules, .git, etc.
        _skip_dirs = {'.git', '__pycache__', 'node_modules', '.pytest_cache', 'htmlcov', '.tox', 'venv', '.venv'}
        
        files = []
        for item in sorted(full_path.rglob("*")):
            # Skip files inside ignored directories
            if any(part in _skip_dirs for part in item.parts):
                continue
            if item.is_file():
                rel = item.relative_to(workspace)
                size = item.stat().st_size
                files.append(f"  {rel} ({size} bytes)")
        
        if not files:
            return f"Directory {directory} is empty"
        
        return f"All files under {directory}:\n" + "\n".join(files)
    except Exception as e:
        return f"❌ Error listing {directory}: {str(e)}"


def file_deleter(file_path: str, workspace_path: Optional[str] = None) -> str:
    """Delete a file in the workspace. Use this when the user asks to remove or delete a file.
    Do NOT empty the file with file_writer — call file_deleter to remove the file from the filesystem.

    Args:
        file_path: Path to the file to delete (relative to workspace root). Example: 'src/unused.js'
        workspace_path: Optional workspace root (for thread-safe use). If not set, uses WORKSPACE_PATH env.

    Returns:
        Success or error message
    """
    try:
        workspace = _resolve_workspace(workspace_path)
        full_path = (workspace / file_path).resolve()
        workspace_resolved = workspace.resolve()
        try:
            full_path.relative_to(workspace_resolved)
        except ValueError:
            return f"❌ Refused: {file_path} is outside the workspace."
        if ".." in file_path or file_path.startswith("/"):
            return f"❌ Refused: invalid path {file_path}."
        if not full_path.exists():
            return f"❌ File not found: {file_path}"
        if full_path.is_dir():
            return f"❌ Refused: {file_path} is a directory. file_deleter only removes files."

        full_path.unlink()
        return f"✅ Deleted file: {file_path}"
    except Exception as e:
        logger.error(f"Error deleting {file_path}: {e}")
        return f"❌ Error deleting {file_path}: {str(e)}"


# Create FunctionTool instances (use env WORKSPACE_PATH when called by agent)
FileWriterTool = FunctionTool.from_defaults(
    fn=file_writer,
    name="file_writer",
    description="Write content to a file. Creates parent directories if needed. Use this tool to create or update any file in the workspace."
)

FileReaderTool = FunctionTool.from_defaults(
    fn=file_reader,
    name="file_reader",
    description="Read content from a file in the workspace."
)

FileListTool = FunctionTool.from_defaults(
    fn=file_lister,
    name="file_lister",
    description="Recursively list all files in a directory. Returns file paths relative to workspace and sizes."
)

FileDeleterTool = FunctionTool.from_defaults(
    fn=file_deleter,
    name="file_deleter",
    description="Delete a file from the workspace. Use when the user asks to remove or delete a file. Do NOT empty the file with file_writer — use file_deleter to remove it from the filesystem."
)


def create_workspace_file_tools(workspace_path: Path):
    """Create file tools bound to a specific workspace path (thread-safe, no env).
    Returns (FileWriterTool, FileReaderTool, FileListTool, FileDeleterTool) for use in RefinementAgent."""
    ws = str(workspace_path)
    return [
        FunctionTool.from_defaults(
            fn=partial(file_writer, workspace_path=ws),
            name="file_writer",
            description="Write content to a file. Creates parent directories if needed. Use this tool to create or update any file in the workspace."
        ),
        FunctionTool.from_defaults(
            fn=partial(file_reader, workspace_path=ws),
            name="file_reader",
            description="Read content from a file in the workspace."
        ),
        FunctionTool.from_defaults(
            fn=partial(file_lister, workspace_path=ws),
            name="file_lister",
            description="Recursively list all files in a directory. Returns file paths relative to workspace and sizes."
        ),
        FunctionTool.from_defaults(
            fn=partial(file_deleter, workspace_path=ws),
            name="file_deleter",
            description="Delete a file from the workspace. Use when the user asks to remove or delete a file. Do NOT empty the file with file_writer — use file_deleter to remove it from the filesystem."
        ),
        FunctionTool.from_defaults(
            fn=partial(file_line_replacer, workspace_path=ws),
            name="file_line_replacer",
            description="Replace a range of lines in a file. start_line and end_line are 1-indexed and inclusive."
        ),
    ]
