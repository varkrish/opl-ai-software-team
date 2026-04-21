"""
Unit tests for McpBridge — wraps MCP server tools as LlamaIndex FunctionTools.
TDD: Written before implementation.

All MCP SDK calls are mocked; no real MCP server is needed.
"""
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock
import sys
import asyncio
import types

root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "src"))

if "llama_index.llms.ollama" not in sys.modules:
    sys.modules["llama_index.llms.ollama"] = MagicMock()
if "llama_index.embeddings.huggingface" not in sys.modules:
    sys.modules["llama_index.embeddings.huggingface"] = MagicMock()


def _make_mcp_tool_def(name, description="A test tool", input_schema=None):
    """Create a mock MCP tool definition."""
    tool_def = MagicMock()
    tool_def.name = name
    tool_def.description = description
    tool_def.inputSchema = input_schema or {
        "type": "object",
        "properties": {"arg1": {"type": "string", "description": "An argument"}},
        "required": ["arg1"],
    }
    return tool_def


def _make_call_result(text="result text", is_error=False):
    """Create a mock CallToolResult."""
    block = MagicMock()
    block.text = text
    result = MagicMock()
    result.isError = is_error
    result.content = [block]
    return result


class TestMcpBridgeToolDiscovery:

    def test_discovers_all_tools_when_no_allow_list(self):
        """With empty tools allow-list, all server tools should be wrapped."""
        from llamaindex_crew.tools.mcp_bridge import McpBridge
        from llamaindex_crew.config.secure_config import McpToolEntry
        from llama_index.core.tools import FunctionTool

        entry = McpToolEntry(server_name="test", command="echo", args=[])

        tool_defs = [_make_mcp_tool_def("add"), _make_mcp_tool_def("subtract")]
        list_result = MagicMock()
        list_result.tools = tool_defs

        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock()
        mock_session.list_tools = AsyncMock(return_value=list_result)

        bridge = McpBridge(entry)
        bridge._session = mock_session

        tools = bridge._discover_and_wrap()

        assert len(tools) == 2
        names = {t.metadata.name for t in tools}
        assert "mcp_test_add" in names
        assert "mcp_test_subtract" in names

    def test_filters_by_allow_list(self):
        """Only tools in the allow-list should be wrapped."""
        from llamaindex_crew.tools.mcp_bridge import McpBridge
        from llamaindex_crew.config.secure_config import McpToolEntry

        entry = McpToolEntry(server_name="gh", url="http://x", tools=["create_issue"])

        tool_defs = [_make_mcp_tool_def("create_issue"), _make_mcp_tool_def("delete_repo")]
        list_result = MagicMock()
        list_result.tools = tool_defs

        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock()
        mock_session.list_tools = AsyncMock(return_value=list_result)

        bridge = McpBridge(entry)
        bridge._session = mock_session

        tools = bridge._discover_and_wrap()

        assert len(tools) == 1
        assert tools[0].metadata.name == "mcp_gh_create_issue"

    def test_tool_name_prefixed_with_server(self):
        """Tool names should be prefixed: mcp_{server_name}_{tool_name}."""
        from llamaindex_crew.tools.mcp_bridge import McpBridge
        from llamaindex_crew.config.secure_config import McpToolEntry

        entry = McpToolEntry(server_name="jira", command="python", args=[])

        tool_defs = [_make_mcp_tool_def("get_issue")]
        list_result = MagicMock()
        list_result.tools = tool_defs

        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock()
        mock_session.list_tools = AsyncMock(return_value=list_result)

        bridge = McpBridge(entry)
        bridge._session = mock_session

        tools = bridge._discover_and_wrap()
        assert tools[0].metadata.name == "mcp_jira_get_issue"

    def test_tool_description_from_mcp(self):
        """Tool description should come from the MCP tool definition."""
        from llamaindex_crew.tools.mcp_bridge import McpBridge
        from llamaindex_crew.config.secure_config import McpToolEntry

        entry = McpToolEntry(server_name="s", command="x", args=[])

        tool_defs = [_make_mcp_tool_def("my_tool", description="Does something special")]
        list_result = MagicMock()
        list_result.tools = tool_defs

        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock()
        mock_session.list_tools = AsyncMock(return_value=list_result)

        bridge = McpBridge(entry)
        bridge._session = mock_session

        tools = bridge._discover_and_wrap()
        assert "Does something special" in tools[0].metadata.description


class TestMcpBridgeToolExecution:

    def test_wrapped_tool_calls_mcp_session(self):
        """Calling a wrapped tool should invoke session.call_tool."""
        from llamaindex_crew.tools.mcp_bridge import McpBridge
        from llamaindex_crew.config.secure_config import McpToolEntry

        entry = McpToolEntry(server_name="s", command="x", args=[])

        tool_defs = [_make_mcp_tool_def("echo")]
        list_result = MagicMock()
        list_result.tools = tool_defs

        call_result = _make_call_result("hello back")

        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock()
        mock_session.list_tools = AsyncMock(return_value=list_result)
        mock_session.call_tool = AsyncMock(return_value=call_result)

        bridge = McpBridge(entry)
        bridge._session = mock_session

        tools = bridge._discover_and_wrap()
        result = tools[0].call(arg1="hello")

        mock_session.call_tool.assert_called_once()
        assert "hello back" in str(result)

    def test_error_result_returns_error_message(self):
        """If MCP returns isError=True, the tool should return an error string."""
        from llamaindex_crew.tools.mcp_bridge import McpBridge
        from llamaindex_crew.config.secure_config import McpToolEntry

        entry = McpToolEntry(server_name="s", command="x", args=[])

        tool_defs = [_make_mcp_tool_def("fail_tool")]
        list_result = MagicMock()
        list_result.tools = tool_defs

        call_result = _make_call_result("something went wrong", is_error=True)

        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock()
        mock_session.list_tools = AsyncMock(return_value=list_result)
        mock_session.call_tool = AsyncMock(return_value=call_result)

        bridge = McpBridge(entry)
        bridge._session = mock_session

        tools = bridge._discover_and_wrap()
        result = tools[0].call(arg1="x")

        assert "error" in str(result).lower()


class TestMcpBridgeObservability:

    def test_logs_on_tool_discovery(self, caplog):
        """Bridge should log when discovering tools from an MCP server."""
        import logging
        from llamaindex_crew.tools.mcp_bridge import McpBridge
        from llamaindex_crew.config.secure_config import McpToolEntry

        entry = McpToolEntry(server_name="obs_test", command="x", args=[])

        tool_defs = [_make_mcp_tool_def("t1")]
        list_result = MagicMock()
        list_result.tools = tool_defs

        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock()
        mock_session.list_tools = AsyncMock(return_value=list_result)

        bridge = McpBridge(entry)
        bridge._session = mock_session

        with caplog.at_level(logging.INFO, logger="llamaindex_crew.tools.mcp_bridge"):
            bridge._discover_and_wrap()

        assert any("obs_test" in r.message for r in caplog.records)
