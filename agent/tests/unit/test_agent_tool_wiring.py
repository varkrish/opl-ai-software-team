"""
Unit tests for agent tool wiring — verifying that DevAgent and FrontendAgent
pick up extra tools from config.
TDD: Written before implementation.
"""
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
import sys
import types

root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "src"))

if "llama_index.llms.ollama" not in sys.modules:
    sys.modules["llama_index.llms.ollama"] = MagicMock()
if "llama_index.embeddings.huggingface" not in sys.modules:
    sys.modules["llama_index.embeddings.huggingface"] = MagicMock()

from llama_index.core.tools import FunctionTool


def _dummy_tool(name: str) -> FunctionTool:
    return FunctionTool.from_defaults(fn=lambda: name, name=name, description=f"dummy {name}")


def _make_config(global_tools=None, agent_tools=None, skills_url=None):
    """Build a minimal SecretConfig dict for testing."""
    from llamaindex_crew.config.secure_config import SecretConfig
    data = {
        "llm": {"api_key": "test-key"},
        "tools": {},
    }
    if global_tools:
        data["tools"]["global_tools"] = global_tools
    if agent_tools:
        data["tools"]["agent_tools"] = agent_tools
    if skills_url:
        data["skills"] = {"service_url": skills_url}
    return SecretConfig(**data)


class TestDevAgentToolWiring:

    @patch("llamaindex_crew.agents.base_agent.get_llm_for_agent")
    def test_dev_agent_has_builtin_tools_when_no_config(self, mock_llm):
        """Without tool config, DevAgent should have its standard tools."""
        mock_llm.return_value = MagicMock()

        config = _make_config()
        with patch("llamaindex_crew.agents.dev_agent.load_tools", return_value=[]):
            with patch("llamaindex_crew.agents.dev_agent.ConfigLoader") as mock_cl:
                mock_cl.load.return_value = config
                from llamaindex_crew.agents.dev_agent import DevAgent
                agent = DevAgent()

        tool_names = {t.metadata.name for t in agent.agent.tools}
        assert "file_writer" in tool_names
        assert "git_init" in tool_names

    @patch("llamaindex_crew.agents.base_agent.get_llm_for_agent")
    def test_dev_agent_includes_extra_tools_from_config(self, mock_llm):
        """DevAgent should include tools resolved from config entries."""
        mock_llm.return_value = MagicMock()

        extra = _dummy_tool("extra_skill_query")
        config = _make_config(
            agent_tools={
                "developer": [
                    {"type": "native", "module": "m", "name": "F"},
                ],
            },
        )
        with patch("llamaindex_crew.agents.dev_agent.load_tools", return_value=[extra]):
            with patch("llamaindex_crew.agents.dev_agent.ConfigLoader") as mock_cl:
                mock_cl.load.return_value = config
                from llamaindex_crew.agents.dev_agent import DevAgent
                agent = DevAgent()

        tool_names = {t.metadata.name for t in agent.agent.tools}
        assert "extra_skill_query" in tool_names
        assert "file_writer" in tool_names


class TestFrontendAgentToolWiring:

    @patch("llamaindex_crew.agents.base_agent.get_llm_for_agent")
    def test_frontend_agent_includes_extra_tools(self, mock_llm):
        """FrontendAgent should include tools resolved from config entries."""
        mock_llm.return_value = MagicMock()

        extra = _dummy_tool("frontend_skill")
        config = _make_config(
            agent_tools={
                "frontend": [
                    {"type": "native", "module": "m", "name": "F"},
                ],
            },
        )
        with patch("llamaindex_crew.agents.frontend_agent.load_tools", return_value=[extra]):
            with patch("llamaindex_crew.agents.frontend_agent.ConfigLoader") as mock_cl:
                mock_cl.load.return_value = config
                from llamaindex_crew.agents.frontend_agent import FrontendAgent
                agent = FrontendAgent()

        tool_names = {t.metadata.name for t in agent.agent.tools}
        assert "frontend_skill" in tool_names
        assert "file_writer" in tool_names


class TestToolWiringObservability:

    @patch("llamaindex_crew.agents.base_agent.get_llm_for_agent")
    def test_dev_agent_logs_extra_tools_loaded(self, mock_llm, caplog):
        """DevAgent should log the number of extra tools loaded."""
        import logging
        mock_llm.return_value = MagicMock()

        config = _make_config()
        with patch("llamaindex_crew.agents.dev_agent.load_tools", return_value=[]):
            with patch("llamaindex_crew.agents.dev_agent.ConfigLoader") as mock_cl:
                mock_cl.load.return_value = config
                with caplog.at_level(logging.INFO, logger="llamaindex_crew.agents.dev_agent"):
                    from llamaindex_crew.agents.dev_agent import DevAgent
                    agent = DevAgent()

        assert any("tool" in r.message.lower() for r in caplog.records)
