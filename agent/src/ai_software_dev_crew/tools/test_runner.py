"""
Test execution tools for AI agents
"""
import subprocess
from pathlib import Path
from crewai.tools import BaseTool
from pydantic import Field
import os


class PytestRunnerTool(BaseTool):
    name: str = "pytest_runner"
    description: str = "Run pytest tests in the workspace. Returns test results and coverage."
    
    workspace_path: str = Field(default_factory=lambda: os.getenv("WORKSPACE_PATH", "./workspace"))
    
    def _run(self, test_path: str = "tests/", verbose: bool = True) -> str:
        """Run pytest tests"""
        try:
            workspace = Path(self.workspace_path)
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


class CodeCoverageTool(BaseTool):
    name: str = "code_coverage"
    description: str = "Run pytest with coverage analysis. Returns coverage percentage and report."
    
    workspace_path: str = Field(default_factory=lambda: os.getenv("WORKSPACE_PATH", "./workspace"))
    
    def _run(self, source_path: str = "src/") -> str:
        """Run tests with coverage"""
        try:
            workspace = Path(self.workspace_path)
            
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


