"""
E2E Test Configuration and Fixtures
"""
import pytest
import os
import tempfile
import shutil
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
# E2E uses Flask test client without JWT headers; override .env auth defaults.
os.environ["AUTH_ENABLED"] = "false"


@pytest.fixture(scope="session")
def e2e_base_workspace():
    """Create base temporary workspace for E2E tests"""
    workspace = Path(tempfile.mkdtemp(prefix="e2e_test_"))
    yield workspace
    # Cleanup after all tests
    if workspace.exists():
        shutil.rmtree(workspace)


@pytest.fixture(scope="function")
def e2e_workspace(e2e_base_workspace):
    """Create test-specific workspace directory"""
    import uuid
    test_id = str(uuid.uuid4())[:8]
    workspace = e2e_base_workspace / f"test_{test_id}"
    workspace.mkdir(parents=True, exist_ok=True)
    yield workspace
    # Workspace cleaned up by session fixture


@pytest.fixture(autouse=True)
def set_e2e_env(e2e_workspace, monkeypatch):
    """Set E2E test environment variables"""
    monkeypatch.setenv("WORKSPACE_PATH", str(e2e_workspace))
    monkeypatch.setenv("PROJECT_ID", f"e2e_test_{e2e_workspace.name}")
    monkeypatch.setenv("BUDGET_MAX_COST_PER_PROJECT", "10.0")
    monkeypatch.setenv("BUDGET_MAX_COST_PER_HOUR", "5.0")
    monkeypatch.setenv("AUTH_ENABLED", "false")
    try:
        import crew_studio.auth as auth_mod
        monkeypatch.setattr(auth_mod, "AUTH_ENABLED", False)
    except ImportError:
        pass
    
    # Ensure we have API keys for E2E tests
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))
    from llamaindex_crew.config import ConfigLoader
    
    try:
        config = ConfigLoader.load()
        has_key = bool(config.llm.api_key)
    except Exception:
        has_key = False
        
    if not has_key and not os.getenv("OPENROUTER_API_KEY") and not os.getenv("OPENAI_API_KEY") and not os.getenv("LLM_API_KEY"):
        pytest.skip("E2E tests require an LLM API key in config.yaml or environment variables")

@pytest.fixture
def calculator_vision():
    """Standard calculator vision for E2E tests"""
    return """Create a simple Python calculator with add and subtract functions.
The calculator should have a Calculator class with add(a, b) and subtract(a, b) methods.
Include unit tests using pytest."""


@pytest.fixture
def todo_api_vision():
    """TODO API vision for E2E tests"""
    return """Create a simple TODO API with FastAPI.
Features:
- Create TODO item
- List TODO items
- Mark TODO as complete
- Delete TODO item
Include pytest tests and store TODOs in memory."""


@pytest.fixture
def travel_planner_vision():
    """Voyager-style full-path vision (Redis/caching in approved spec)."""
    return (
        "Create an AI-powered Travel Planner UI called Voyager for families "
        "to plan trips easily. Web UI with itinerary generation."
)


@pytest.fixture
def google_html_vision():
    """Fast-path HTML mock vision."""
    return (
        "Create a simple HTML page that mocks a Google search homepage. "
        "Pure HTML and CSS only, single index.html."
)


def verify_workflow_outputs(workspace: Path, expected_files: list[str] = None) -> dict:
    """
    Verify workflow generated expected outputs
    
    Args:
        workspace: Workspace path
        expected_files: List of expected file paths relative to workspace
    
    Returns:
        dict with verification results
    """
    results = {
        "artifacts_present": True,
        "code_generated": False,
        "tests_generated": False,
        "missing_files": [],
        "generated_files": []
    }
    
    # Check standard artifacts
    standard_artifacts = [
        "requirements.md",
        "user_stories.md", 
        "design_spec.md",
        "tech_stack.md"
    ]
    
    for artifact in standard_artifacts:
        artifact_path = workspace / artifact
        if not artifact_path.exists():
            results["artifacts_present"] = False
            results["missing_files"].append(artifact)
    
    # Check for code files
    code_patterns = ["*.py", "*.js", "*.ts", "*.jsx", "*.tsx"]
    code_files = []
    for pattern in code_patterns:
        code_files.extend(workspace.rglob(pattern))
    
    # Exclude test files from code count
    non_test_code = [f for f in code_files if "test" not in f.name.lower()]
    results["code_generated"] = len(non_test_code) > 0
    results["generated_files"].extend([str(f.relative_to(workspace)) for f in non_test_code])
    
    # Check for test files
    test_patterns = ["test_*.py", "*_test.py", "*.test.js", "*.test.ts"]
    test_files = []
    for pattern in test_patterns:
        test_files.extend(workspace.rglob(pattern))
    
    results["tests_generated"] = len(test_files) > 0
    results["generated_files"].extend([str(f.relative_to(workspace)) for f in test_files])
    
    # Check expected files if provided
    if expected_files:
        for expected_file in expected_files:
            file_path = workspace / expected_file
            if not file_path.exists():
                results["missing_files"].append(expected_file)
    
    return results
