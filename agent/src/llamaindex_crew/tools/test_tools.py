"""
Test execution tools for AI agents
Migrated from CrewAI BaseTool to LlamaIndex FunctionTool
"""
import subprocess
from pathlib import Path
from llama_index.core.tools import FunctionTool
import os


def pytest_runner(test_path: str = "tests/", verbose: bool = True) -> str:
    """Run pytest tests in the workspace. Returns test results and coverage.
    
    Args:
        test_path: Path to test directory or file (default: "tests/")
        verbose: Whether to run in verbose mode (default: True)
    
    Returns:
        Test results or error message
    """
    try:
        workspace_path = os.getenv("WORKSPACE_PATH", "./workspace")
        workspace = Path(workspace_path)
        full_path = workspace / test_path
        
        if not full_path.exists():
            return f"❌ Test path not found: {test_path}"
        
        # Build pytest command
        cmd = ["pytest", str(full_path)]
        if verbose:
            cmd.append("-v")
        cmd.extend(["--tb=short", "--color=yes"])
        
        # Run tests
        result = subprocess.run(
            cmd,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout
        )
        
        output = result.stdout + result.stderr
        
        if result.returncode == 0:
            return f"✅ All tests passed!\n\n{output}"
        else:
            return f"❌ Some tests failed (exit code: {result.returncode})\n\n{output}"
            
    except subprocess.TimeoutExpired:
        return "❌ Tests timed out after 5 minutes"
    except Exception as e:
        return f"❌ Error running tests: {str(e)}"


def code_coverage(source_path: str = "src/") -> str:
    """Run pytest with coverage analysis. Returns coverage percentage and report.
    
    Args:
        source_path: Path to source code directory (default: "src/")
    
    Returns:
        Coverage report or error message
    """
    try:
        workspace_path = os.getenv("WORKSPACE_PATH", "./workspace")
        workspace = Path(workspace_path)
        
        # Build pytest command with coverage
        cmd = [
            "pytest",
            "--cov=" + source_path,
            "--cov-report=term-missing",
            "--cov-report=html",
            "-v"
        ]
        
        # Run tests with coverage
        result = subprocess.run(
            cmd,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=300
        )
        
        output = result.stdout + result.stderr
        
        # Extract coverage percentage
        for line in output.split('\n'):
            if 'TOTAL' in line and '%' in line:
                return f"📊 Coverage Report:\n\n{output}\n\nCoverage report saved to htmlcov/index.html"
        
        return f"📊 Coverage Report:\n\n{output}"
            
    except subprocess.TimeoutExpired:
        return "❌ Coverage analysis timed out after 5 minutes"
    except Exception as e:
        return f"❌ Error running coverage: {str(e)}"


# Create FunctionTool instances
PytestRunnerTool = FunctionTool.from_defaults(
    fn=pytest_runner,
    name="pytest_runner",
    description="Run pytest tests in the workspace. Returns test results and coverage."
)

CodeCoverageTool = FunctionTool.from_defaults(
    fn=code_coverage,
    name="code_coverage",
    description="Run pytest with coverage analysis. Returns coverage percentage and report."
)
