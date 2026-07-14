from pathlib import Path

from llamaindex_crew.tools.file_tools import file_writer, set_thread_workspace, clear_thread_workspace
from llamaindex_crew.utils.execution_log import (
    append_execution_log,
    log_tool_invocation,
    truncate_for_log,
)


def test_truncate_for_log():
    text = "x" * 1000
    out = truncate_for_log(text, limit=100)
    assert len(out) < len(text)
    assert "[content truncated]" in out


def test_append_execution_log_uses_thread_workspace(tmp_path):
    set_thread_workspace(str(tmp_path))
    try:
        append_execution_log("hello from pipeline")
        log_tool_invocation(
            "file_writer",
            {"file_path": "a.go", "content": "package main\n" * 50},
            result="✅ wrote",
            status="ok",
        )
        log = (tmp_path / "execution.log").read_text(encoding="utf-8")
        assert "hello from pipeline" in log
        assert "[Tool: file_writer]" in log
        assert "a.go" in log
        assert "[content truncated]" in log or "characters)" in log
    finally:
        clear_thread_workspace()


def test_file_writer_decorator_logs_rejections_only(tmp_path):
    set_thread_workspace(str(tmp_path))
    try:
        ok = file_writer("hello.txt", "hello world", workspace_path=str(tmp_path))
        assert "Successfully wrote" in ok
        log_path = tmp_path / "execution.log"
        if log_path.exists():
            assert "[Tool: file_writer]" not in log_path.read_text(encoding="utf-8")

        rejected = file_writer("unknown", "package main", workspace_path=str(tmp_path))
        assert rejected.startswith("❌")
        log = (tmp_path / "execution.log").read_text(encoding="utf-8")
        assert "[Tool: file_writer]" in log
        assert "status=error" in log
        assert "Rejected" in log
    finally:
        clear_thread_workspace()
