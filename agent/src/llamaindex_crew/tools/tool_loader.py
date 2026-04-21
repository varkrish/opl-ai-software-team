"""
Config-driven tool loader.

Resolves ToolEntry configs into LlamaIndex FunctionTool instances.
Supports two source types:
  - native: import a Python module and call a factory / grab a symbol
  - mcp:    connect to an MCP server and bridge its tools
"""
import importlib
import logging
import time
from typing import List

from llama_index.core.tools import FunctionTool

from ..config.secure_config import NativeToolEntry, McpToolEntry
from ..utils.observability import log_tool_call, log_tool_error, TraceContext

logger = logging.getLogger(__name__)

ToolEntry = NativeToolEntry | McpToolEntry


def load_tools(entries: List[ToolEntry]) -> List[FunctionTool]:
    """Resolve a list of tool entries into FunctionTool instances.

    Failures are logged and skipped so the agent can still start.
    """
    ctx = TraceContext()
    span = ctx.new_span("load_tools", entry_count=len(entries))
    span.start()

    tools: List[FunctionTool] = []
    for entry in entries:
        t0 = time.monotonic()
        try:
            if getattr(entry, "type", None) == "native":
                loaded = _load_native_tool(entry)
                tools.extend(loaded)
                log_tool_call(_entry_label(entry), {}, f"{len(loaded)} tool(s)", (time.monotonic() - t0) * 1000)
            elif getattr(entry, "type", None) == "mcp":
                loaded = _load_mcp_tools(entry)
                tools.extend(loaded)
                log_tool_call(_entry_label(entry), {}, f"{len(loaded)} tool(s)", (time.monotonic() - t0) * 1000)
            else:
                logger.warning("Unknown tool entry type: %s — skipping", getattr(entry, "type", None))
        except Exception as exc:
            log_tool_error(_entry_label(entry), exc, (time.monotonic() - t0) * 1000)
            logger.warning(
                "Failed to load tool entry %s — skipping",
                _entry_label(entry),
                exc_info=True,
            )

    span.end()
    logger.info(
        "Tool loading complete: %d entries -> %d tools in %.1fms",
        len(entries), len(tools), span.duration_ms,
    )
    return tools


def _load_native_tool(entry: NativeToolEntry) -> List[FunctionTool]:
    logger.info("Loading native tool %s.%s", entry.module, entry.name)
    module = importlib.import_module(entry.module)
    symbol = getattr(module, entry.name)
    if isinstance(symbol, FunctionTool):
        return [symbol]
    if callable(symbol):
        result = symbol(**entry.config)
        return [result] if isinstance(result, FunctionTool) else list(result)
    raise TypeError(f"{entry.module}.{entry.name} is neither callable nor a FunctionTool")


def _load_mcp_tools(entry: McpToolEntry) -> List[FunctionTool]:
    logger.info("Loading MCP tools from server '%s'", entry.server_name)
    from .mcp_bridge import McpBridge
    bridge = McpBridge(entry)
    return bridge.as_function_tools()


def _entry_label(entry: ToolEntry) -> str:
    if isinstance(entry, NativeToolEntry):
        return f"native:{entry.module}.{entry.name}"
    if isinstance(entry, McpToolEntry):
        return f"mcp:{entry.server_name}"
    return str(entry)
