"""
Unit tests for observability utilities — structured logging, trace context.
TDD: Written before implementation.
"""
import pytest
import json
import logging
from pathlib import Path
from unittest.mock import MagicMock
import sys

root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "src"))

if "llama_index.llms.ollama" not in sys.modules:
    sys.modules["llama_index.llms.ollama"] = MagicMock()
if "llama_index.embeddings.huggingface" not in sys.modules:
    sys.modules["llama_index.embeddings.huggingface"] = MagicMock()


class TestStructuredFormatter:

    def test_outputs_json(self):
        from llamaindex_crew.utils.observability import StructuredFormatter
        formatter = StructuredFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="test.py",
            lineno=1, msg="hello", args=(), exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["message"] == "hello"
        assert parsed["level"] == "INFO"
        assert "timestamp" in parsed

    def test_includes_extra_fields(self):
        from llamaindex_crew.utils.observability import StructuredFormatter
        formatter = StructuredFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="test.py",
            lineno=1, msg="op", args=(), exc_info=None,
        )
        record.tool_name = "skill_query"
        record.duration_ms = 42.5
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["tool_name"] == "skill_query"
        assert parsed["duration_ms"] == 42.5

    def test_includes_exception_info(self):
        from llamaindex_crew.utils.observability import StructuredFormatter
        formatter = StructuredFormatter()
        try:
            raise ValueError("test error")
        except ValueError:
            record = logging.LogRecord(
                name="test", level=logging.ERROR, pathname="test.py",
                lineno=1, msg="fail", args=(), exc_info=sys.exc_info(),
            )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert "exception" in parsed
        assert "ValueError" in parsed["exception"]


class TestTraceContext:

    def test_generates_trace_id(self):
        from llamaindex_crew.utils.observability import TraceContext
        ctx = TraceContext()
        assert len(ctx.trace_id) == 32  # hex string

    def test_generates_span_id(self):
        from llamaindex_crew.utils.observability import TraceContext
        ctx = TraceContext()
        span = ctx.new_span("test_op")
        assert len(span.span_id) == 16

    def test_span_has_operation_name(self):
        from llamaindex_crew.utils.observability import TraceContext
        ctx = TraceContext()
        span = ctx.new_span("load_tools")
        assert span.operation == "load_tools"

    def test_span_timing(self):
        import time
        from llamaindex_crew.utils.observability import TraceContext
        ctx = TraceContext()
        span = ctx.new_span("slow_op")
        span.start()
        time.sleep(0.01)
        span.end()
        assert span.duration_ms >= 5  # at least 5ms given sleep

    def test_span_to_dict(self):
        from llamaindex_crew.utils.observability import TraceContext
        ctx = TraceContext()
        span = ctx.new_span("test")
        span.start()
        span.end()
        d = span.to_dict()
        assert d["trace_id"] == ctx.trace_id
        assert d["operation"] == "test"
        assert "span_id" in d
        assert "duration_ms" in d


class TestToolCallLogger:

    def test_logs_tool_invocation(self, caplog):
        from llamaindex_crew.utils.observability import log_tool_call
        with caplog.at_level(logging.INFO, logger="llamaindex_crew.utils.observability"):
            log_tool_call("skill_query", {"query": "frappe"}, "result text", duration_ms=15.3)
        assert any("skill_query" in r.message for r in caplog.records)

    def test_logs_tool_error(self, caplog):
        from llamaindex_crew.utils.observability import log_tool_error
        with caplog.at_level(logging.WARNING, logger="llamaindex_crew.utils.observability"):
            log_tool_error("mcp_jira_create", ValueError("bad input"), duration_ms=5.0)
        assert any("mcp_jira_create" in r.message for r in caplog.records)
