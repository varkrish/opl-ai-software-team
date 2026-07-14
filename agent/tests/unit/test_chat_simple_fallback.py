"""Tests for BaseLlamaIndexAgent.chat_simple runtime fallback."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "src"))

from llamaindex_crew.agents.base_agent import BaseLlamaIndexAgent


@pytest.fixture
def react_agent():
    with patch("llamaindex_crew.agents.base_agent.get_llm_for_agent") as mock_llm_factory:
        mock_llm = MagicMock()
        mock_llm.callback_manager = MagicMock()
        mock_llm_factory.return_value = mock_llm
        agent = BaseLlamaIndexAgent(
            role="Developer",
            goal="Write code",
            backstory="Expert dev",
            tools=[],
            agent_type="worker",
        )
        agent.budget_tracker.check_budget_safe = MagicMock(return_value={"allowed": True})
        agent.budget_tracker.record_usage = MagicMock()
        yield agent, mock_llm


def test_chat_simple_bypasses_react_agent(react_agent):
    wrapper, mock_llm = react_agent
    mock_llm.chat.return_value = MagicMock(__str__=lambda self: "package main\n")

    result = wrapper.chat_simple("Output main.go as a code fence.")

    mock_llm.chat.assert_called_once()
    messages = mock_llm.chat.call_args[0][0]
    assert messages[0].role == "system"
    assert "NO tools" in messages[0].content
    assert messages[1].role == "user"
    assert result == "package main\n"
    # Underlying ReAct/FC agent must not be invoked
    assert not hasattr(wrapper.agent, "chat") or wrapper.agent.chat != mock_llm.chat


def test_dev_agent_still_uses_supports_react_for_construction():
    """Config-level supports_react must still control tool wiring at init."""
    from llamaindex_crew.agents.dev_agent import DevAgent

    with patch("llamaindex_crew.agents.dev_agent.get_supports_react", return_value=True):
        with patch("llamaindex_crew.agents.dev_agent.BaseLlamaIndexAgent") as mock_base:
            DevAgent(workspace_path=Path("/tmp/ws"))
            tools = mock_base.call_args.kwargs.get("tools") or mock_base.call_args[1].get("tools")
            assert len(tools) > 0

    with patch("llamaindex_crew.agents.dev_agent.get_supports_react", return_value=False):
        with patch("llamaindex_crew.agents.dev_agent.BaseLlamaIndexAgent") as mock_base:
            DevAgent(workspace_path=Path("/tmp/ws"))
            tools = mock_base.call_args.kwargs.get("tools") or mock_base.call_args[1].get("tools")
            assert tools == []
