"""Support-oriented execution.log — prompts, tool calls, rejections; not full LLM bodies."""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, Optional

from llama_index.core.callbacks.base_handler import BaseCallbackHandler
from llama_index.core.callbacks.schema import CBEventType, EventPayload

from .execution_log import truncate_for_log

logger = logging.getLogger(__name__)

_PROMPT_CHAR_LIMIT = 300
_TOOL_ARG_CHAR_LIMIT = 200
_TOOL_RESULT_CHAR_LIMIT = 500


class CleanJobLogger(BaseCallbackHandler):
    """
    Writes support-monitoring events to workspace/execution.log:

    - Truncated LLM prompts (on request)
    - Tool call arguments (truncated)
    - Tool results (truncated)
    - LLM response metadata only (char count / code blocks) — never the full body
    """

    def __init__(self, log_file_path: str):
        super().__init__(event_starts_to_ignore=[], event_ends_to_ignore=[])
        self.log_file_path = log_file_path
        if self.log_file_path:
            os.makedirs(os.path.dirname(self.log_file_path), exist_ok=True)

    def _write_log(self, message: str) -> None:
        if not self.log_file_path:
            return
        try:
            with open(self.log_file_path, "a", encoding="utf-8") as f:
                f.write(message + "\n")
        except OSError:
            pass

    def on_event_start(
        self,
        event_type: CBEventType,
        payload: Optional[Dict[str, Any]] = None,
        event_id: str = "",
        **kwargs: Any,
    ) -> str:
        if payload is None:
            return event_id

        if event_type == CBEventType.LLM:
            messages = payload.get(EventPayload.MESSAGES, [])
            log_msgs = []
            for msg in messages:
                content = msg.content or ""
                role = getattr(msg, "role", "unknown")
                role_str = str(role).lower()

                is_code_or_tool = (
                    "tool" in role_str and len(content) > 100
                    or content.strip().startswith(("package ", "import ", "def ", "class "))
                )
                if is_code_or_tool:
                    content = f"[code/file content omitted] ({len(content)} characters)"
                elif len(content) > _PROMPT_CHAR_LIMIT:
                    content = (
                        content[:_PROMPT_CHAR_LIMIT].strip()
                        + f"\n... [truncated] ({len(content) - _PROMPT_CHAR_LIMIT} characters)"
                    )
                log_msgs.append(f"{role_str}: {content}")

            self._write_log(
                "=== [LLM Prompt] ===\n"
                + "\n".join(log_msgs)
                + "\n====================\n"
            )

        elif event_type == CBEventType.FUNCTION_CALL:
            tool = payload.get(EventPayload.TOOL)
            tool_name = payload.get("tool_name") or "unknown"
            if tool_name == "unknown" and tool:
                tool_name = (
                    getattr(tool, "name", None)
                    or getattr(getattr(tool, "metadata", None), "name", "unknown")
                )
            arguments = payload.get("arguments") or {}

            clean_args: Dict[str, Any] = {}
            for key, value in arguments.items():
                if isinstance(value, str) and len(value) > _TOOL_ARG_CHAR_LIMIT:
                    clean_args[key] = (
                        value[:_TOOL_ARG_CHAR_LIMIT]
                        + f"... [code truncated] ({len(value) - _TOOL_ARG_CHAR_LIMIT} characters)"
                    )
                else:
                    clean_args[key] = value

            self._write_log(
                f"=== [Tool Call: {tool_name}] ===\n"
                f"Arguments: {json.dumps(clean_args, indent=2, default=str)}\n"
                "========================\n"
            )

        return event_id

    def on_event_end(
        self,
        event_type: CBEventType,
        payload: Optional[Dict[str, Any]] = None,
        event_id: str = "",
        **kwargs: Any,
    ) -> None:
        if payload is None:
            return

        if event_type == CBEventType.LLM:
            response = payload.get(EventPayload.RESPONSE)
            if not (response and hasattr(response, "message") and response.message.content):
                return
            content = response.message.content
            code_blocks = len(re.findall(r"```", content)) // 2
            self._write_log(
                "=== [LLM Response] ===\n"
                f"({len(content)} characters"
                + (f", {code_blocks} code block(s)" if code_blocks else "")
                + " — body omitted for support log)\n"
                "======================\n"
            )

        elif event_type == CBEventType.FUNCTION_CALL:
            tool_output = payload.get(EventPayload.FUNCTION_OUTPUT)
            if tool_output is not None:
                preview = truncate_for_log(
                    str(tool_output),
                    limit=_TOOL_RESULT_CHAR_LIMIT,
                    label="tool output",
                )
                self._write_log(
                    f"=== [Tool Result] ===\n{preview}\n=====================\n"
                )

    def start_trace(self, trace_id: Optional[str] = None, **kwargs: Any) -> None:
        pass

    def end_trace(
        self,
        trace_id: Optional[str] = None,
        trace_payload: Optional[Dict[str, Any]] = None,
        trace_obj: Optional[Any] = None,
        **kwargs: Any,
    ) -> None:
        pass
