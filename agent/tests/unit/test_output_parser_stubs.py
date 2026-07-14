"""Tests for rejecting DeepSeek channel tokens and LLM stub content."""
import sys
import tempfile
from pathlib import Path

import pytest

root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "src"))

from llamaindex_crew.utils.output_parser import (
    clean_llm_response_text,
    extract_files_from_response,
    is_agent_planning_monologue,
    is_llm_stub_content,
    looks_like_raw_agent_dump,
    response_needs_simple_retry,
    write_files_from_response,
)


CHANNEL_STUB = "<|channel|>commentary<|message|>We will output action.<|end|>"

# Real corruption from job 032ce963 — ReAct planning text written as metrics_handler.go
METRICS_HANDLER_MONOLOGUE = """\
We cannot run code search tool yet. Use code_search.

We need to use tool code_search.

Let's search for metrics usage.

Probably the metrics handler should expose /metrics endpoint returning Prometheus default metrics.

Implementation: package handler; import net/http, github.com/prometheus/client_golang/prometheus/promhttp.

We'll call file_reader with path.
"""

VALID_GO_HANDLER = """\
package handler

import (
    "net/http"

    "github.com/prometheus/client_golang/prometheus/promhttp"
)

func MetricsHandler() http.Handler {
    return promhttp.Handler()
}
"""


class TestPlanningMonologueDetection:
    def test_metrics_handler_monologue_detected(self):
        assert is_agent_planning_monologue(
            METRICS_HANDLER_MONOLOGUE,
            file_path="internal/handler/metrics_handler.go",
        )

    def test_real_go_handler_not_planning(self):
        assert not is_agent_planning_monologue(
            VALID_GO_HANDLER,
            file_path="internal/handler/metrics_handler.go",
        )

    def test_python_code_not_planning(self):
        code = "import os\n\ndef main():\n    print('hi')\n"
        assert not is_agent_planning_monologue(code, file_path="app.py")

    def test_markdown_spec_allows_prose(self):
        spec = (
            "We will use Go for the API.\n\n"
            "# Solution Specification\n\n"
            "## Architecture\n\n"
            "The API exposes REST endpoints.\n"
        )
        assert not is_agent_planning_monologue(spec, file_path="solution_spec.md")

    def test_response_needs_simple_retry_for_planning_monologue(self):
        assert response_needs_simple_retry(
            METRICS_HANDLER_MONOLOGUE,
            target_file_path="internal/handler/metrics_handler.go",
        )

    def test_planning_monologue_not_extracted_as_raw_source(self):
        entries, strategy = extract_files_from_response(
            METRICS_HANDLER_MONOLOGUE,
            target_file_path="internal/handler/metrics_handler.go",
        )
        assert entries == []
        assert strategy == "none"

    def test_planning_monologue_not_written_to_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            result = write_files_from_response(
                METRICS_HANDLER_MONOLOGUE,
                ws,
                target_file_path="internal/handler/metrics_handler.go",
            )
            assert result.written_paths == []
            assert not (ws / "internal/handler/metrics_handler.go").exists()


class TestLlmStubDetection:
    def test_channel_token_response_is_stub(self):
        assert is_llm_stub_content(CHANNEL_STUB)
        assert looks_like_raw_agent_dump(CHANNEL_STUB)

    def test_stripped_inner_message_is_stub(self):
        inner = clean_llm_response_text(CHANNEL_STUB)
        assert inner == ""
        assert is_llm_stub_content("We will output action.")

    def test_multiline_doc_with_we_will_opening_is_not_stub(self):
        spec = (
            "We will use Go for the API and React for the UI.\n\n"
            "# Solution Specification\n\n"
            "## Architecture\n\n"
            "The API exposes REST endpoints.\n"
        )
        assert not is_llm_stub_content(spec)

    def test_response_needs_simple_retry_for_channel_stub(self):
        from llamaindex_crew.utils.output_parser import response_needs_simple_retry
        assert response_needs_simple_retry(CHANNEL_STUB, target_file_path="handlers.go")

    def test_response_needs_simple_retry_false_for_valid_fence(self):
        from llamaindex_crew.utils.output_parser import response_needs_simple_retry
        response = "```go\npackage main\n\nfunc main() {}\n```"
        assert not response_needs_simple_retry(response, target_file_path="main.go")

    def test_real_code_is_not_stub(self):
        code = "package main\n\nfunc main() {\n\tfmt.Println(\"hi\")\n}\n"
        assert not is_llm_stub_content(code)
        assert not looks_like_raw_agent_dump(code)


class TestExtractAndWriteGuards:
    def test_channel_stub_not_extracted_as_file(self):
        entries, strategy = extract_files_from_response(
            CHANNEL_STUB,
            target_file_path="internal/api/handlers.go",
        )
        assert entries == []
        assert strategy == "none"

    def test_channel_stub_not_written_to_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            result = write_files_from_response(
                CHANNEL_STUB,
                ws,
                target_file_path="solution_spec.md",
            )
            assert result.written_paths == []
            assert not (ws / "solution_spec.md").exists()

    def test_valid_json_still_writes(self):
        response = (
            '[{"file_path": "main.go", "content": "package main\\n\\n'
            'func main() {}\\n"}]'
        )
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            result = write_files_from_response(response, ws, target_file_path="main.go")
            assert "main.go" in result.written_paths
            assert "package main" in (ws / "main.go").read_text()


class TestCodeValidatorChannelStub:
    def test_channel_stub_file_incomplete(self):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "handlers.go"
            f.write_text(CHANNEL_STUB)
            result = CodeCompletenessValidator.validate_file(f)
            assert result["complete"] is False
            assert any("channel" in i.lower() or "stub" in i.lower() for i in result["issues"])

    def test_planning_monologue_go_file_incomplete(self):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "metrics_handler.go"
            f.write_text(METRICS_HANDLER_MONOLOGUE)
            result = CodeCompletenessValidator.validate_file(f)
            assert result["complete"] is False
            assert any("planning" in i.lower() for i in result["issues"])
