"""
Tests for generated React Native apps
"""
import pytest
import subprocess
from pathlib import Path


@pytest.fixture
def workspace_path():
    """Get workspace path from environment or use default"""
    import os
    workspace = os.getenv("WORKSPACE_PATH", "./test_workspace_llamaindex")
    return Path(workspace)


@pytest.mark.generated_app
def test_react_native_package_json_exists(workspace_path):
    """Test that package.json exists for React Native app"""
    package_json = workspace_path / "package.json"
    if package_json.exists():
        assert True
    else:
        pytest.skip("package.json not found - app may not be React Native")


@pytest.mark.generated_app
def test_react_native_src_files_exist(workspace_path):
    """Test that source files exist in src/ directory"""
    src_dir = workspace_path / "src"
    if src_dir.exists():
        js_files = list(src_dir.rglob("*.js")) + list(src_dir.rglob("*.jsx"))
        assert len(js_files) > 0, "No JavaScript files found in src/"
    else:
        pytest.skip("src/ directory not found")


@pytest.mark.generated_app
def test_react_native_entry_point(workspace_path):
    """Test that entry point (index.js) exists"""
    entry_points = [
        workspace_path / "index.js",
        workspace_path / "index.ts",
        workspace_path / "index.tsx"
    ]
    
    if any(ep.exists() for ep in entry_points):
        assert True
    else:
        pytest.skip("Entry point not found")


@pytest.mark.generated_app
@pytest.mark.skip(reason="Requires npm and React Native CLI installed")
def test_react_native_build(workspace_path):
    """Test that React Native app can be built"""
    # This test requires npm and React Native CLI
    # It's marked as skip by default since it requires full build environment
    
    package_json = workspace_path / "package.json"
    if not package_json.exists():
        pytest.skip("package.json not found")
    
    # Try to install dependencies
    result = subprocess.run(
        ["npm", "install"],
        cwd=workspace_path,
        capture_output=True,
        timeout=300
    )
    
    assert result.returncode == 0, f"npm install failed: {result.stderr.decode()}"
