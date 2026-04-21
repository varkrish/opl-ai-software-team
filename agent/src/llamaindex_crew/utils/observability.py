"""
Observability utilities — structured logging, trace context, and tool call logging.

Provides:
  - StructuredFormatter: JSON log formatter for machine-parseable output
  - TraceContext / Span: lightweight W3C-style trace/span IDs
  - log_tool_call / log_tool_error: convenience loggers for tool invocations

Compatible with OpenTelemetry when available, but works standalone.
"""
import json
import logging
import os
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Standard keys excluded from extra-field extraction
_STANDARD_KEYS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys())


class StructuredFormatter(logging.Formatter):
    """JSON log formatter that captures standard fields + arbitrary extras."""

    def format(self, record: logging.LogRecord) -> str:
        entry: Dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for key, val in record.__dict__.items():
            if key not in _STANDARD_KEYS and not key.startswith("_"):
                entry[key] = val

        if record.exc_info and record.exc_info[1]:
            entry["exception"] = "".join(traceback.format_exception(*record.exc_info))

        return json.dumps(entry, default=str)


@dataclass
class Span:
    """A lightweight trace span."""
    trace_id: str
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    operation: str = ""
    _start_ns: int = 0
    _end_ns: int = 0
    attributes: Dict[str, Any] = field(default_factory=dict)

    def start(self):
        self._start_ns = time.monotonic_ns()

    def end(self):
        self._end_ns = time.monotonic_ns()

    @property
    def duration_ms(self) -> float:
        if self._end_ns and self._start_ns:
            return (self._end_ns - self._start_ns) / 1_000_000
        return 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "operation": self.operation,
            "duration_ms": round(self.duration_ms, 2),
            **self.attributes,
        }


class TraceContext:
    """Lightweight trace context — generates W3C-compatible trace/span IDs."""

    def __init__(self, trace_id: Optional[str] = None):
        self.trace_id = trace_id or uuid.uuid4().hex

    def new_span(self, operation: str, **attributes) -> Span:
        return Span(trace_id=self.trace_id, operation=operation, attributes=attributes)


def log_tool_call(tool_name: str, args: Dict[str, Any], result: Any, duration_ms: float = 0.0):
    """Log a successful tool invocation with structured metadata."""
    logger.info(
        "Tool call: %s completed in %.1fms",
        tool_name, duration_ms,
        extra={
            "tool_name": tool_name,
            "tool_args": list(args.keys()) if args else [],
            "result_length": len(str(result)),
            "duration_ms": round(duration_ms, 1),
            "event": "tool_call",
        },
    )


def log_tool_error(tool_name: str, error: Exception, duration_ms: float = 0.0):
    """Log a failed tool invocation."""
    logger.warning(
        "Tool error: %s failed after %.1fms — %s: %s",
        tool_name, duration_ms, type(error).__name__, error,
        extra={
            "tool_name": tool_name,
            "error_type": type(error).__name__,
            "error_message": str(error),
            "duration_ms": round(duration_ms, 1),
            "event": "tool_error",
        },
    )


def configure_structured_logging(level: str = "INFO"):
    """Configure the root logger with JSON-structured output.

    Call once at application startup for machine-parseable logs.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(StructuredFormatter())
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers = [handler]
