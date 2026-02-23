
import pytest
from unittest.mock import MagicMock, patch
from agent.src.ai_software_dev_crew.refactor.agents.architect_agent import RefactorArchitectAgent

@pytest.fixture
def mock_base_init():
    with patch('agent.src.llamaindex_crew.agents.base_agent.BaseLlamaIndexAgent.__init__', return_value=None) as mock:
        yield mock

@pytest.fixture
def mock_file_tools():
    with patch('agent.src.ai_software_dev_crew.refactor.agents.architect_agent.create_workspace_file_tools') as mock:
        mock.return_value = []
        yield mock

def test_init(mock_base_init, mock_file_tools):
    mock_tools = []
    mock_file_tools.return_value = mock_tools
    
    agent = RefactorArchitectAgent("/tmp/workspace", "job-123")
    
    mock_file_tools.assert_called_once_with("/tmp/workspace")
    mock_base_init.assert_called_once()
    _, kwargs = mock_base_init.call_args
    assert kwargs['role'] == "Refactor Architect"
    assert kwargs['tools'] == mock_tools

def test_analyze(mock_base_init, mock_file_tools):
    # Since we mocked __init__, the 'agent' attribute is not set.
    # We need to set it manually on the instance.
    architect = RefactorArchitectAgent("/tmp/workspace", "job-123")
    architect.agent = MagicMock()
    architect.agent.chat.return_value = "Analysis Complete"

    result = architect.analyze("/tmp/workspace/src")
    
    assert result == "Analysis Complete"
    architect.agent.chat.assert_called_once()
    args, _ = architect.agent.chat.call_args
    assert "ANALYZE MODE" in args[0]
    assert "/tmp/workspace/src" in args[0]

def test_design(mock_base_init, mock_file_tools):
    """design() produces target_architecture.md — the future-state blueprint."""
    architect = RefactorArchitectAgent("/tmp/workspace", "job-123")
    architect.agent = MagicMock()
    architect.agent.chat.return_value = "Design Complete"

    result = architect.design("Java 17", tech_preferences="Use Quarkus")

    assert result == "Design Complete"
    architect.agent.chat.assert_called_once()
    args, _ = architect.agent.chat.call_args
    prompt = args[0]
    assert "DESIGN MODE" in prompt
    assert "Java 17" in prompt
    assert "Use Quarkus" in prompt
    assert "target_architecture.md" in prompt, "Must instruct writing target_architecture.md"

    # Architecture rules must be in the design prompt
    assert "Cloud Native Architect" in prompt, "Prompt missing Architect role"
    assert "12-Factor" in prompt or "12-factor" in prompt.lower(), "Prompt missing 12-factor"
    assert "Bounded Contexts" in prompt, "Prompt missing DDD instruction"
    assert "Cloud Native" in prompt, "Prompt missing Cloud Native instruction"


def test_design_prompt_includes_component_layout(mock_base_init, mock_file_tools):
    """design() prompt asks for component layout, directory structure, and mapping."""
    architect = RefactorArchitectAgent("/tmp/workspace", "job-123")
    architect.agent = MagicMock()
    architect.agent.chat.return_value = "Done"

    architect.design("Python 3.12, FastAPI")

    args, _ = architect.agent.chat.call_args
    prompt = args[0]
    assert "Component" in prompt or "Service Layout" in prompt
    assert "Directory Structure" in prompt
    assert "Mapping" in prompt or "Current" in prompt


def test_plan(mock_base_init, mock_file_tools):
    architect = RefactorArchitectAgent("/tmp/workspace", "job-123")
    architect.agent = MagicMock()
    architect.agent.chat.return_value = "Plan Created"

    result = architect.plan("Java 17", tech_preferences="Use Testcontainers")
    
    assert result == "Plan Created"
    architect.agent.chat.assert_called_once()
    args, _ = architect.agent.chat.call_args
    assert "PLANNING MODE" in args[0]
    assert "Java 17" in args[0]
    assert "Use Testcontainers" in args[0]
    
    # Cloud Native & DDD Assertions
    assert "Cloud Native Architect" in args[0], "Prompt missing Architect role"
    assert "12-factor" in args[0].lower(), "Prompt missing 12-factor instruction"
    assert "Bounded Contexts" in args[0], "Prompt missing DDD instruction"


def test_plan_prompt_references_target_architecture(mock_base_init, mock_file_tools):
    """plan() prompt must reference target_architecture.md as input."""
    architect = RefactorArchitectAgent("/tmp/workspace", "job-123")
    architect.agent = MagicMock()
    architect.agent.chat.return_value = "Plan Created"

    architect.plan("Java 17")

    args, _ = architect.agent.chat.call_args
    prompt = args[0]
    assert "target_architecture.md" in prompt, (
        "Plan prompt must reference target_architecture.md so the LLM uses it"
    )
    assert "current_architecture.md" in prompt, (
        "Plan prompt must reference current_architecture.md"
    )


def test_plan_prompt_requires_workspace_relative_paths(mock_base_init, mock_file_tools):
    """Fix 5: plan prompt must instruct the LLM to emit workspace-relative paths."""
    architect = RefactorArchitectAgent("/tmp/workspace", "job-123")
    architect.agent = MagicMock()
    architect.agent.chat.return_value = "Plan Created"

    architect.plan("Java 17")

    args, _ = architect.agent.chat.call_args
    prompt = args[0].lower()
    assert "relative" in prompt, "Prompt must instruct workspace-relative file paths"
