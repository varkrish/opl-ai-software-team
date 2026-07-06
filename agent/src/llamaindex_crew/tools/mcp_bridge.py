"""
MCP Bridge — wraps an MCP server's tools as LlamaIndex FunctionTools.

Supports stdio (command + args) and SSE/HTTP (url) transports.
Tool names are prefixed with ``mcp_{server_name}_`` to avoid collisions.
"""
import asyncio
import logging
import os
import urllib.parse
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Environment scrubbing
# ---------------------------------------------------------------------------

# Substrings that must never appear in env var keys passed to MCP subprocesses.
# Checked case-insensitively as a substring match (not just prefix) so that
# keys like MY_TOKEN or DB_PASSWORD are caught as well as TOKEN or PASSWORD.
_BLOCKED_ENV_PREFIXES = (
    "api_key",
    "apikey",
    "secret",
    "password",
    "passwd",
    "token",
    "credential",
    "keycloak",
    "jwt",
    "auth_",
    "aws_",
    "azure_",
    "gcp_",
    "google_api",
)

# Benign runtime vars that are safe to inherit (PATH, locale, home, etc.)
_ALLOWED_ENV_KEYS = frozenset({
    "PATH",
    "HOME",
    "USER",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "TMPDIR",
    "TMP",
    "TEMP",
    "XDG_RUNTIME_DIR",
    "NODE_PATH",
    "PYTHONPATH",
    "VIRTUAL_ENV",
    "npm_config_cache",
})


def _safe_env(user_env: Dict[str, str]) -> Dict[str, str]:
    """Build a scrubbed env for stdio MCP subprocesses.

    Policy:
    - Start from an empty base (no implicit os.environ inheritance).
    - Allow a small set of safe runtime vars from the host.
    - Block anything matching known secret/credential key patterns.
    - Merge in the user-supplied env last (they can add what their
      server genuinely needs, but cannot override blocked keys).
    """
    base: Dict[str, str] = {}

    for key, val in os.environ.items():
        key_lower = key.lower()
        if any(sub in key_lower for sub in _BLOCKED_ENV_PREFIXES):
            continue
        if key in _ALLOWED_ENV_KEYS:
            base[key] = val

    # User-supplied env: apply after base but still block secret key names
    for key, val in user_env.items():
        key_lower = key.lower()
        if any(sub in key_lower for sub in _BLOCKED_ENV_PREFIXES):
            logger.warning(
                "MCP env scrub: blocked user-supplied key '%s' (matches secret pattern)",
                key,
            )
            continue
        base[key] = val

    return base


# ---------------------------------------------------------------------------
# SSE URL validation
# ---------------------------------------------------------------------------

_PRIVATE_PREFIXES = (
    "169.254.",  # link-local
    "10.",
    "192.168.",
)
_PRIVATE_RANGES_172 = range(16, 32)  # 172.16–172.31


def _validate_sse_url(url: str) -> None:
    """Reject SSE URLs that would allow SSRF to private/internal endpoints."""
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception as exc:
        raise ValueError(f"MCP SSE URL is invalid: {exc}") from exc

    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"MCP SSE URL scheme '{parsed.scheme}' is not allowed (use http or https)"
        )

    hostname = parsed.hostname or ""
    if hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        raise ValueError(
            f"MCP SSE URL hostname '{hostname}' is not allowed (loopback address)"
        )
    if any(hostname.startswith(p) for p in _PRIVATE_PREFIXES):
        raise ValueError(
            f"MCP SSE URL hostname '{hostname}' is not allowed (private address range)"
        )
    if hostname.startswith("172."):
        parts = hostname.split(".")
        if len(parts) >= 2 and parts[1].isdigit() and int(parts[1]) in _PRIVATE_RANGES_172:
            raise ValueError(
                f"MCP SSE URL hostname '{hostname}' is not allowed (private address range)"
            )

from pydantic import create_model
from llama_index.core.tools import FunctionTool

from ..config.secure_config import McpToolEntry

logger = logging.getLogger(__name__)


class McpBridge:
    """Connect to an MCP server, discover its tools, and wrap them as FunctionTools."""

    def __init__(self, entry: McpToolEntry):
        self.entry = entry
        self._session = None
        self._cleanup_callbacks: list = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def as_function_tools(self) -> List[FunctionTool]:
        """Connect to the MCP server and return wrapped FunctionTools.

        Runs the async connection in the current event loop (or creates one).
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import nest_asyncio
            nest_asyncio.apply()
            loop.run_until_complete(self._connect())
        else:
            asyncio.run(self._async_connect_wrapper())

        return self._discover_and_wrap()

    def _discover_and_wrap(self) -> List[FunctionTool]:
        """Discover tools from an already-connected session and wrap them."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        list_result = loop.run_until_complete(self._session.list_tools())

        tools: List[FunctionTool] = []
        for tool_def in list_result.tools:
            if self.entry.tools and tool_def.name not in self.entry.tools:
                continue
            tools.append(self._wrap_tool(tool_def))

        logger.info(
            "MCP server '%s': discovered %d tools, wrapped %d (allow-list: %s)",
            self.entry.server_name,
            len(list_result.tools),
            len(tools),
            self.entry.tools or "all",
        )
        return tools

    def close(self):
        """Tear down transport resources."""
        for cb in self._cleanup_callbacks:
            try:
                cb()
            except Exception:
                logger.debug("Cleanup callback error", exc_info=True)

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    async def _connect(self):
        if self._session is not None:
            return

        if self.entry.command:
            await self._connect_stdio()
        elif self.entry.url:
            await self._connect_sse()
        else:
            raise ValueError(
                f"MCP entry '{self.entry.server_name}' has neither command nor url"
            )

    async def _async_connect_wrapper(self):
        await self._connect()

    async def _connect_stdio(self):
        from mcp import StdioServerParameters
        from mcp.client.stdio import stdio_client
        from mcp import ClientSession

        safe = _safe_env(self.entry.env or {})
        logger.debug(
            "MCP stdio '%s': scrubbed env has %d keys (user supplied %d)",
            self.entry.server_name,
            len(safe),
            len(self.entry.env or {}),
        )
        params = StdioServerParameters(
            command=self.entry.command,
            args=self.entry.args,
            env=safe,
        )
        read, write = await stdio_client(params).__aenter__()
        self._session = ClientSession(read, write)
        await self._session.__aenter__()
        await self._session.initialize()

    async def _connect_sse(self):
        from mcp.client.sse import sse_client
        from mcp import ClientSession

        _validate_sse_url(self.entry.url)
        logger.debug("MCP SSE '%s': connecting to %s", self.entry.server_name, self.entry.url)
        read, write = await sse_client(self.entry.url).__aenter__()
        self._session = ClientSession(read, write)
        await self._session.__aenter__()
        await self._session.initialize()

    # ------------------------------------------------------------------
    # Tool wrapping
    # ------------------------------------------------------------------

    def _wrap_tool(self, tool_def) -> FunctionTool:
        session = self._session
        tool_name = tool_def.name
        prefixed_name = f"mcp_{self.entry.server_name}_{tool_name}"

        def call_mcp_tool(**kwargs) -> str:
            logger.info("Calling MCP tool %s with args %s", prefixed_name, list(kwargs.keys()))
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            result = loop.run_until_complete(
                session.call_tool(tool_name, arguments=kwargs)
            )
            if result.isError:
                msg = "\n".join(
                    getattr(b, "text", str(b)) for b in result.content
                )
                logger.warning("MCP tool %s returned error: %s", prefixed_name, msg)
                return f"MCP tool error: {msg}"

            return "\n".join(
                b.text for b in result.content if hasattr(b, "text")
            )

        fn_schema = self._build_schema(tool_def, prefixed_name)

        return FunctionTool.from_defaults(
            fn=call_mcp_tool,
            name=prefixed_name,
            description=tool_def.description or f"MCP tool: {tool_name}",
            fn_schema=fn_schema,
        )

    @staticmethod
    def _build_schema(tool_def, prefixed_name: str):
        """Convert MCP inputSchema (JSON Schema) to a Pydantic model for LlamaIndex."""
        input_schema = getattr(tool_def, "inputSchema", None) or {}
        properties = input_schema.get("properties", {})
        required = set(input_schema.get("required", []))

        type_map = {
            "string": str,
            "integer": int,
            "number": float,
            "boolean": bool,
        }

        fields: Dict[str, Any] = {}
        for prop_name, prop_def in properties.items():
            py_type = type_map.get(prop_def.get("type", "string"), str)
            if prop_name in required:
                fields[prop_name] = (py_type, ...)
            else:
                fields[prop_name] = (Optional[py_type], None)

        model_name = prefixed_name.replace("-", "_").replace(".", "_") + "_Schema"
        return create_model(model_name, **fields)
