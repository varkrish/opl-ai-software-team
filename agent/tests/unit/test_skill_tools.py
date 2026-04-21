"""
Unit tests for SkillQueryTool factory.
TDD: Written before implementation.
"""
import pytest
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import sys

root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "src"))

if "llama_index.llms.ollama" not in sys.modules:
    sys.modules["llama_index.llms.ollama"] = MagicMock()
if "llama_index.embeddings.huggingface" not in sys.modules:
    sys.modules["llama_index.embeddings.huggingface"] = MagicMock()


class TestSkillQueryToolFactory:

    def test_returns_function_tool(self):
        from llamaindex_crew.tools.skill_tools import SkillQueryTool
        from llama_index.core.tools import FunctionTool
        tool = SkillQueryTool(service_url="http://localhost:8090")
        assert isinstance(tool, FunctionTool)

    def test_tool_name_is_skill_query(self):
        from llamaindex_crew.tools.skill_tools import SkillQueryTool
        tool = SkillQueryTool(service_url="http://localhost:8090")
        assert tool.metadata.name == "skill_query"

    def test_tool_has_description(self):
        from llamaindex_crew.tools.skill_tools import SkillQueryTool
        tool = SkillQueryTool(service_url="http://localhost:8090")
        assert len(tool.metadata.description) > 10


class TestSkillQueryToolExecution:

    def _make_tool(self, **kwargs):
        from llamaindex_crew.tools.skill_tools import SkillQueryTool
        return SkillQueryTool(service_url="http://test:8090", **kwargs)

    def test_query_without_tags(self):
        """Query without tags should POST to /query without tags field."""
        tool = self._make_tool()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {"skill_name": "frappe-api", "content": "Use @whitelist", "tags": ["python"]},
            ]
        }

        with patch("httpx.post", return_value=mock_resp) as mock_post:
            result = tool.call(query="whitelist decorator")

        call_kwargs = mock_post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert "tags" not in payload
        assert "whitelist" in str(result)

    def test_query_with_explicit_tags(self):
        """Query with tags should include them in the POST body."""
        tool = self._make_tool()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"results": []}

        with patch("httpx.post", return_value=mock_resp) as mock_post:
            result = tool.call(query="hooks", tags=["frappe"])

        payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1].get("json")
        assert payload["tags"] == ["frappe"]

    def test_query_with_default_tags(self):
        """When default_tags is set, it should be used if no explicit tags."""
        tool = self._make_tool(default_tags=["python", "backend"])

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"results": []}

        with patch("httpx.post", return_value=mock_resp) as mock_post:
            tool.call(query="test")

        payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1].get("json")
        assert payload["tags"] == ["python", "backend"]

    def test_explicit_tags_override_defaults(self):
        """Explicit tags should override default_tags."""
        tool = self._make_tool(default_tags=["python"])

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"results": []}

        with patch("httpx.post", return_value=mock_resp) as mock_post:
            tool.call(query="test", tags=["react"])

        payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1].get("json")
        assert payload["tags"] == ["react"]

    def test_service_unavailable_returns_empty(self):
        """If skills service is unreachable, return empty string (graceful degradation)."""
        tool = self._make_tool()

        with patch("httpx.post", side_effect=ConnectionError("refused")):
            result = tool.call(query="anything")

        assert str(result) == ""

    def test_empty_results(self):
        """Zero matches should return a readable message."""
        tool = self._make_tool()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"results": []}

        with patch("httpx.post", return_value=mock_resp):
            result = tool.call(query="nonexistent skill")

        assert "no matching" in str(result).lower()

    def test_top_k_forwarded(self):
        """top_k should be passed through to the API."""
        tool = self._make_tool()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"results": []}

        with patch("httpx.post", return_value=mock_resp) as mock_post:
            tool.call(query="test", top_k=5)

        payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1].get("json")
        assert payload["top_k"] == 5
