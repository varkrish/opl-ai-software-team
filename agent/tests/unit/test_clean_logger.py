import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock

# Under TDD, we import CleanJobLogger (which does not exist yet)
from llamaindex_crew.utils.clean_logger import CleanJobLogger
from llama_index.core.callbacks.schema import CBEventType, EventPayload
from llama_index.core.llms import ChatMessage, MessageRole

def test_clean_logger_truncates_llm_prompts(tmp_path):
    log_file = tmp_path / "execution.log"
    logger = CleanJobLogger(str(log_file))
    
    # Simulate LLM start event with a huge prompt
    huge_prompt = "A" * 1000
    messages = [ChatMessage(role=MessageRole.USER, content=huge_prompt)]
    
    logger.on_event_start(
        event_type=CBEventType.LLM,
        payload={EventPayload.MESSAGES: messages},
        event_id="test-event-id"
    )
    
    log_content = log_file.read_text()
    assert "[LLM Prompt]" in log_content
    # Huge prompt should be truncated, so we shouldn't see 1000 'A's in a single block
    assert "A" * 1000 not in log_content
    assert "A" * 100 in log_content  # first 100 chars allowed
    assert "[truncated]" in log_content or "..." in log_content

def test_clean_logger_truncates_tool_call_code(tmp_path):
    log_file = tmp_path / "execution.log"
    logger = CleanJobLogger(str(log_file))
    
    # Simulate tool call (e.g. file_writer) with huge content
    huge_code = "def my_func():\n" + "    print('hello')\n" * 100
    tool_args = {"file_path": "test.py", "content": huge_code}
    
    logger.on_event_start(
        event_type=CBEventType.FUNCTION_CALL,
        payload={
            EventPayload.TOOL: MagicMock(name="file_writer"),
            "tool_name": "file_writer",
            "arguments": tool_args
        },
        event_id="test-event-id"
    )
    
    log_content = log_file.read_text()
    assert "[Tool Call: file_writer]" in log_content
    assert "test.py" in log_content
    # The huge code should be truncated, so it shouldn't be printed in full
    assert "print('hello')" * 100 not in log_content
    assert "[code truncated]" in log_content or "[truncated]" in log_content

def test_clean_logger_llm_response_metadata_only(tmp_path):
    log_file = tmp_path / "execution.log"
    logger = CleanJobLogger(str(log_file))

    markdown_response = (
        "Here is the code:\n"
        "```python\n"
        "def main():\n"
        "    print('code block')\n"
        "```\n"
        "Let me know if this works."
    )

    response_mock = MagicMock()
    response_mock.message.content = markdown_response

    logger.on_event_end(
        event_type=CBEventType.LLM,
        payload={EventPayload.RESPONSE: response_mock},
        event_id="test-event-id",
    )

    log_content = log_file.read_text()
    assert "[LLM Response]" in log_content
    assert "body omitted" in log_content
    assert f"{len(markdown_response)} characters" in log_content
    assert "print('code block')" not in log_content
    assert "Here is the code" not in log_content

def test_clean_logger_logs_tool_result_on_end(tmp_path):
    log_file = tmp_path / "execution.log"
    logger = CleanJobLogger(str(log_file))

    logger.on_event_end(
        event_type=CBEventType.FUNCTION_CALL,
        payload={EventPayload.FUNCTION_OUTPUT: "✅ Successfully wrote to main.go (120 chars)"},
        event_id="test-event-id",
    )

    log_content = log_file.read_text()
    assert "[Tool Result]" in log_content
    assert "main.go" in log_content
