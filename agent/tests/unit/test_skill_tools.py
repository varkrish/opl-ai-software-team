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

    def test_low_relevance_results_are_dropped(self):
        """Results scoring below MIN_SKILL_SCORE must not be surfaced as matches."""
        tool = self._make_tool()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {"skill_name": "react-component-style", "content": "Use hooks", "tags": ["react"], "score": 0.55},
            ]
        }

        with patch("httpx.post", return_value=mock_resp):
            result = tool.call(query="Apache Camel route builder")

        assert "no matching" in str(result).lower()
        assert "react-component-style" not in str(result)

    def test_high_relevance_results_are_kept(self):
        """Results scoring above MIN_SKILL_SCORE should still be surfaced."""
        tool = self._make_tool()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {"skill_name": "frappe-api-patterns", "content": "Use @whitelist", "tags": ["frappe"], "score": 0.82},
            ]
        }

        with patch("httpx.post", return_value=mock_resp):
            result = tool.call(query="Frappe whitelist decorator")

        assert "frappe-api-patterns" in str(result)

    def test_missing_score_is_treated_as_legacy_and_kept(self):
        """Older skills-service responses without a score must not be dropped."""
        tool = self._make_tool()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {"skill_name": "frappe-api-patterns", "content": "Use @whitelist", "tags": ["frappe"]},
            ]
        }

        with patch("httpx.post", return_value=mock_resp):
            result = tool.call(query="whitelist")

        assert "frappe-api-patterns" in str(result)


class TestPrefetchSkillsRelevanceFiltering:
    """prefetch_skills() must not inject low-relevance skills as 'ground truth'."""

    def _mock_config(self):
        config = MagicMock()
        config.skills.service_url = "http://test:8090"
        return config

    def test_skips_injection_when_nothing_clears_threshold(self):
        from llamaindex_crew.tools.skill_tools import prefetch_skills

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {"skill_name": "react-component-style", "content": "Use hooks", "tags": ["react"], "score": 0.55},
            ]
        }

        with patch("llamaindex_crew.config.ConfigLoader.load", return_value=self._mock_config()), \
             patch("httpx.post", return_value=mock_resp):
            result = prefetch_skills(vision="Build an Apache Camel integration service", role="tech_architect")

        assert result == ""

    def test_injects_high_relevance_skill(self):
        from llamaindex_crew.tools.skill_tools import prefetch_skills

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {"skill_name": "frappe-api-patterns", "content": "Use @whitelist", "tags": ["frappe"], "score": 0.82},
            ]
        }

        with patch("llamaindex_crew.config.ConfigLoader.load", return_value=self._mock_config()), \
             patch("httpx.post", return_value=mock_resp):
            result = prefetch_skills(vision="Build a Frappe app", role="tech_architect")

        assert "frappe-api-patterns" in result
        assert "Use @whitelist" in result


class TestPrefetchSkillsManifestGating:
    """prefetch_skills must respect locked stack_manifest (stub HTTP only)."""

    def _mock_config(self):
        config = MagicMock()
        config.skills.service_url = "http://test:8090"
        return config

    def _client_manifest(self, workspace: Path):
        from llamaindex_crew.workflows.solutioning_loop import write_stack_manifest

        write_stack_manifest(
            workspace,
            {
                "path": "fast",
                "delivery_surface": "client_deliverable",
                "complexity": "minimal",
                "chosen_stack": ["html", "css", "svg"],
                "forbidden_tiers": ["application_server", "database", "cms_platform"],
                "rationale": "Simple client page",
                "skills_query": "vanilla html svg accessibility",
            },
        )

    def test_drops_conflicting_frappe_skill_when_client_manifest_locked(self, tmp_path):
        from llamaindex_crew.tools.skill_tools import prefetch_skills

        self._client_manifest(tmp_path)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {
                    "skill_name": "frappe-doctype-patterns",
                    "content": "Create DocType and hooks.py",
                    "tags": ["frappe"],
                    "score": 0.91,
                },
                {
                    "skill_name": "html-accessibility",
                    "content": "Use semantic HTML and ARIA",
                    "tags": ["html"],
                    "score": 0.88,
                },
            ]
        }

        with patch("llamaindex_crew.config.ConfigLoader.load", return_value=self._mock_config()), \
             patch("httpx.post", return_value=mock_resp):
            result = prefetch_skills(
                vision="Build a Frappe invoicing app",
                role="tech_architect",
                workspace_path=tmp_path,
            )

        assert "frappe-doctype-patterns" not in result
        assert "html-accessibility" in result

    def test_uses_manifest_skills_query_not_raw_vision_alone(self, tmp_path):
        from llamaindex_crew.tools.skill_tools import prefetch_skills

        self._client_manifest(tmp_path)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"results": []}

        with patch("llamaindex_crew.config.ConfigLoader.load", return_value=self._mock_config()), \
             patch("httpx.post", return_value=mock_resp) as mock_post:
            prefetch_skills(
                vision="Build a Frappe invoicing app with MariaDB",
                role="designer",
                workspace_path=tmp_path,
            )

        assert mock_post.called
        payloads = [
            (c.kwargs.get("json") or c[1].get("json"))
            for c in mock_post.call_args_list
        ]
        queries = " ".join(str(p.get("query", "")) for p in payloads if p)
        assert "vanilla" in queries.lower() or "html" in queries.lower() or "svg" in queries.lower()
        assert "frappe" not in queries.lower()

    def test_no_manifest_keeps_existing_behaviour(self, tmp_path):
        from llamaindex_crew.tools.skill_tools import prefetch_skills

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {"skill_name": "frappe-api-patterns", "content": "Use @whitelist", "tags": ["frappe"], "score": 0.82},
            ]
        }

        with patch("llamaindex_crew.config.ConfigLoader.load", return_value=self._mock_config()), \
             patch("httpx.post", return_value=mock_resp) as mock_post:
            result = prefetch_skills(
                vision="Build a Frappe app",
                role="tech_architect",
                workspace_path=tmp_path,
            )

        assert "frappe-api-patterns" in result
        payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1].get("json")
        assert "Frappe" in payload["query"] or "frappe" in payload["query"].lower()
