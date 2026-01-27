"""
Pytest configuration and fixtures
"""
import pytest
import os
import tempfile
import shutil
from pathlib import Path


@pytest.fixture(scope="session")
def test_workspace():
    """Create temporary workspace for tests"""
    workspace = tempfile.mkdtemp()
    yield Path(workspace)
    shutil.rmtree(workspace)


@pytest.fixture(autouse=True)
def set_test_env(test_workspace, monkeypatch):
    """Set test environment variables"""
    monkeypatch.setenv("WORKSPACE_PATH", str(test_workspace))
    monkeypatch.setenv("PROJECT_ID", "test-project")
    monkeypatch.setenv("BUDGET_MAX_COST_PER_PROJECT", "100.0")
    monkeypatch.setenv("BUDGET_MAX_COST_PER_HOUR", "10.0")
