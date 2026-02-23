
import pytest
from unittest.mock import MagicMock, patch
from agent.src.ai_software_dev_crew.refactor.agents.executor_agent import RefactorExecutorAgent

@pytest.fixture
def mock_base_init():
    with patch('agent.src.llamaindex_crew.agents.base_agent.BaseLlamaIndexAgent.__init__', return_value=None) as mock:
        yield mock

@pytest.fixture
def mock_file_tools():
    with patch('agent.src.ai_software_dev_crew.refactor.agents.executor_agent.create_workspace_file_tools') as mock:
        mock.return_value = []
        yield mock

def test_init(mock_base_init, mock_file_tools):
    mock_tools = []
    mock_file_tools.return_value = mock_tools
    
    agent = RefactorExecutorAgent("/tmp/workspace", "job-123")
    
    mock_file_tools.assert_called_once_with("/tmp/workspace")
    mock_base_init.assert_called_once()
    _, kwargs = mock_base_init.call_args
    assert kwargs['role'] == "Refactor Developer"
    assert kwargs['tools'] == mock_tools

def test_execute_task(mock_base_init, mock_file_tools):
    executor = RefactorExecutorAgent("/tmp/workspace", "job-123")
    executor.agent = MagicMock()
    executor.agent.chat.return_value = "Task Executed"
    
    result = executor.execute_task("src/main.py", "Rename function foo to bar")
    
    assert result == "Task Executed"
    executor.agent.chat.assert_called_once()
    args, _ = executor.agent.chat.call_args
    assert "EXECUTE REFACTOR" in args[0]
    assert "src/main.py" in args[0]
    assert "Rename function foo to bar" in args[0]
