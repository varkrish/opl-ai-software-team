"""
Unit tests for tool_loader — config-driven FunctionTool resolver.
TDD: Written before implementation.
"""
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock
import sys
import types

root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "src"))

if "llama_index.llms.ollama" not in sys.modules:
    sys.modules["llama_index.llms.ollama"] = MagicMock()
if "llama_index.embeddings.huggingface" not in sys.modules:
    sys.modules["llama_index.embeddings.huggingface"] = MagicMock()


class TestLoadNativeTools:

    def test_loads_factory_function(self):
        """A native entry pointing to a callable factory should invoke it with config kwargs."""
        from llamaindex_crew.tools.tool_loader import load_tools
        from llamaindex_crew.config.secure_config import NativeToolEntry
        from llama_index.core.tools import FunctionTool

        dummy_tool = FunctionTool.from_defaults(fn=lambda: "ok", name="dummy", description="d")

        fake_module = types.ModuleType("fake_mod")
        fake_module.MyFactory = MagicMock(return_value=dummy_tool)

        with patch("importlib.import_module", return_value=fake_module):
            entry = NativeToolEntry(module="fake_mod", name="MyFactory", config={"x": 1})
            tools = load_tools([entry])

        assert len(tools) == 1
        assert tools[0] is dummy_tool
        fake_module.MyFactory.assert_called_once_with(x=1)

    def test_loads_prebuilt_function_tool(self):
        """A native entry pointing to a FunctionTool instance should return it directly."""
        from llamaindex_crew.tools.tool_loader import load_tools
        from llamaindex_crew.config.secure_config import NativeToolEntry
        from llama_index.core.tools import FunctionTool

        prebuilt = FunctionTool.from_defaults(fn=lambda: "ok", name="pre", description="d")

        fake_module = types.ModuleType("fake_mod")
        fake_module.PreBuilt = prebuilt

        with patch("importlib.import_module", return_value=fake_module):
            entry = NativeToolEntry(module="fake_mod", name="PreBuilt")
            tools = load_tools([entry])

        assert len(tools) == 1
        assert tools[0] is prebuilt

    def test_empty_entries_returns_empty(self):
        from llamaindex_crew.tools.tool_loader import load_tools
        assert load_tools([]) == []

    def test_bad_module_logs_warning_and_continues(self):
        """If a native module import fails, log warning and skip it."""
        from llamaindex_crew.tools.tool_loader import load_tools
        from llamaindex_crew.config.secure_config import NativeToolEntry

        entry = NativeToolEntry(module="nonexistent.module.xyz", name="Foo")
        tools = load_tools([entry])
        assert tools == []

    def test_mixed_native_entries(self):
        """Multiple native entries should all be resolved."""
        from llamaindex_crew.tools.tool_loader import load_tools
        from llamaindex_crew.config.secure_config import NativeToolEntry
        from llama_index.core.tools import FunctionTool

        t1 = FunctionTool.from_defaults(fn=lambda: "a", name="t1", description="d")
        t2 = FunctionTool.from_defaults(fn=lambda: "b", name="t2", description="d")

        mod1 = types.ModuleType("m1")
        mod1.F1 = MagicMock(return_value=t1)
        mod2 = types.ModuleType("m2")
        mod2.F2 = MagicMock(return_value=t2)

        def side_effect(name):
            return {"m1": mod1, "m2": mod2}[name]

        with patch("importlib.import_module", side_effect=side_effect):
            entries = [
                NativeToolEntry(module="m1", name="F1"),
                NativeToolEntry(module="m2", name="F2"),
            ]
            tools = load_tools(entries)

        assert len(tools) == 2


class TestLoadMcpTools:

    def test_mcp_entry_delegates_to_bridge(self):
        """An MCP entry should instantiate McpBridge and call as_function_tools."""
        from llamaindex_crew.config.secure_config import McpToolEntry
        from llama_index.core.tools import FunctionTool

        dummy = FunctionTool.from_defaults(fn=lambda: "mcp", name="mcp_t", description="d")

        mock_bridge_cls = MagicMock()
        mock_bridge_cls.return_value.as_function_tools.return_value = [dummy]

        fake_bridge_mod = types.ModuleType("llamaindex_crew.tools.mcp_bridge")
        fake_bridge_mod.McpBridge = mock_bridge_cls

        entry = McpToolEntry(server_name="test", command="echo", args=["hi"])

        with patch.dict("sys.modules", {"llamaindex_crew.tools.mcp_bridge": fake_bridge_mod}):
            # re-import to pick up the patched module
            import importlib
            import llamaindex_crew.tools.tool_loader as tl_mod
            importlib.reload(tl_mod)
            tools = tl_mod.load_tools([entry])

        assert len(tools) == 1
        assert tools[0] is dummy
        mock_bridge_cls.assert_called_once_with(entry)

    def test_mcp_bridge_failure_logs_warning_and_continues(self):
        """If MCP bridge fails to connect, log warning and skip."""
        from llamaindex_crew.config.secure_config import McpToolEntry

        mock_bridge_cls = MagicMock()
        mock_bridge_cls.return_value.as_function_tools.side_effect = ConnectionError("fail")

        fake_bridge_mod = types.ModuleType("llamaindex_crew.tools.mcp_bridge")
        fake_bridge_mod.McpBridge = mock_bridge_cls

        entry = McpToolEntry(server_name="broken", url="http://bad:9999")

        with patch.dict("sys.modules", {"llamaindex_crew.tools.mcp_bridge": fake_bridge_mod}):
            import importlib
            import llamaindex_crew.tools.tool_loader as tl_mod
            importlib.reload(tl_mod)
            tools = tl_mod.load_tools([entry])

        assert tools == []


class TestLoadToolsObservability:

    def test_native_load_emits_log(self, caplog):
        """Tool loader should log when loading native tools."""
        import logging
        from llamaindex_crew.tools.tool_loader import load_tools
        from llamaindex_crew.config.secure_config import NativeToolEntry
        from llama_index.core.tools import FunctionTool

        t = FunctionTool.from_defaults(fn=lambda: "x", name="x", description="d")
        mod = types.ModuleType("m")
        mod.T = MagicMock(return_value=t)

        with patch("importlib.import_module", return_value=mod):
            entry = NativeToolEntry(module="m", name="T")
            with caplog.at_level(logging.INFO, logger="llamaindex_crew.tools.tool_loader"):
                load_tools([entry])

        assert any("native" in r.message.lower() or "T" in r.message for r in caplog.records)

    def test_failed_load_emits_warning(self, caplog):
        """Failed tool load should emit a WARNING-level log."""
        import logging
        from llamaindex_crew.tools.tool_loader import load_tools
        from llamaindex_crew.config.secure_config import NativeToolEntry

        entry = NativeToolEntry(module="no.such.module", name="X")
        with caplog.at_level(logging.WARNING, logger="llamaindex_crew.tools.tool_loader"):
            load_tools([entry])

        assert any(r.levelno >= logging.WARNING for r in caplog.records)
