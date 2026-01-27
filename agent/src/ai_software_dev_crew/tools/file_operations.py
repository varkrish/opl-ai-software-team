"""
File operation tools for AI agents
"""
import os
import logging
from pathlib import Path
from crewai.tools import BaseTool
from pydantic import BaseModel, Field
from typing import Type
from ..utils.code_safety import CodeSafetyChecker

logger = logging.getLogger(__name__)


class FileWriterInput(BaseModel):
    """Input schema for FileWriterTool."""
    file_path: str = Field(..., description="Path to the file to write (relative to workspace root). Example: 'index.html' or 'src/main.py'")
    content: str = Field(..., description="The content to write to the file.")


class FileWriterTool(BaseTool):
    name: str = "file_writer"
    description: str = "Write content to a file. Creates parent directories if needed. Use this tool to create or update any file in the workspace."
    args_schema: Type[BaseModel] = FileWriterInput
    
    def _run(self, file_path: str, content: str) -> str:
        """Write content to a file with safety checks"""
        try:
            # Get workspace path from environment
            workspace_path = os.getenv("WORKSPACE_PATH", "./workspace")
            workspace = Path(workspace_path)
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


class FileReaderInput(BaseModel):
    """Input schema for FileReaderTool."""
    file_path: str = Field(..., description="Path to the file to read (relative to workspace root). Example: 'requirements.md' or 'src/main.py'")


class FileReaderTool(BaseTool):
    name: str = "file_reader"
    description: str = "Read content from a file in the workspace."
    args_schema: Type[BaseModel] = FileReaderInput
    
    def _run(self, file_path: str) -> str:
        """Read content from a file"""
        try:
            # Get workspace path from environment
            workspace_path = os.getenv("WORKSPACE_PATH", "./workspace")
            workspace = Path(workspace_path)
            full_path = workspace / file_path
            
            if not full_path.exists():
                return f"❌ File not found: {file_path}"
            
            with open(full_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            return content
        except Exception as e:
            return f"❌ Error reading {file_path}: {str(e)}"


class FileListInput(BaseModel):
    """Input schema for FileListTool."""
    directory: str = Field(default=".", description="Directory path to list (relative to workspace root). Default is '.' (workspace root).")


class FileListTool(BaseTool):
    name: str = "file_lister"
    description: str = "List files in a directory. Returns file names and sizes."
    args_schema: Type[BaseModel] = FileListInput
    
    def _run(self, directory: str = ".") -> str:
        """List files in a directory"""
        try:
            # Get workspace path from environment
            workspace_path = os.getenv("WORKSPACE_PATH", "./workspace")
            workspace = Path(workspace_path)
            full_path = workspace / directory
            
            if not full_path.exists():
                return f"❌ Directory not found: {directory}"
            
            if not full_path.is_dir():
                return f"❌ Not a directory: {directory}"
            
            files = []
            for item in sorted(full_path.iterdir()):
                if item.is_file():
                    size = item.stat().st_size
                    files.append(f"  - {item.name} ({size} bytes)")
                elif item.is_dir():
                    files.append(f"  📁 {item.name}/")
            
            if not files:
                return f"Directory {directory} is empty"
            
            return f"Files in {directory}:\n" + "\n".join(files)
        except Exception as e:
            return f"❌ Error listing {directory}: {str(e)}"


