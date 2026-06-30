"""Unit tests for GitHub search tools — TDD RED first."""
import base64
import json
from unittest.mock import MagicMock, patch

import httpx
import pytest


def _make_search_tool(**kwargs):
    from llamaindex_crew.tools.github_search_tools import GitHubSearchReposTool
    return GitHubSearchReposTool(token="test-token", **kwargs)


def _make_readme_tool(**kwargs):
    from llamaindex_crew.tools.github_search_tools import GitHubRepoReadmeTool
    return GitHubRepoReadmeTool(token="test-token", **kwargs)


def _mock_repo_items(count: int):
    return {
        "items": [
            {
                "full_name": f"org/repo-{i}",
                "description": f"Description {i}",
                "stargazers_count": 100 + i,
                "license": {"spdx_id": "MIT"},
                "topics": ["python", "api"],
            }
            for i in range(count)
        ]
    }


class TestGitHubSearchReposTool:
    def test_search_repos_returns_top_5(self):
        tool = _make_search_tool()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = _mock_repo_items(10)

        with patch("httpx.get", return_value=mock_resp) as mock_get:
            result = tool.call(query="fastapi calculator")

        data = json.loads(str(result))
        assert len(data) == 5
        assert mock_get.call_count == 1
        for item in data:
            assert "full_name" in item
            assert "description" in item
            assert "stars" in item
            assert "topics" in item

    def test_search_repos_rate_limit_enforced(self):
        tool = _make_search_tool(max_calls=10)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = _mock_repo_items(1)

        with patch("httpx.get", return_value=mock_resp) as mock_get:
            for _ in range(10):
                tool.call(query="test")
            blocked = tool.call(query="test")

        assert mock_get.call_count == 10
        assert "rate limit" in str(blocked).lower()

    def test_search_repos_rate_limit_configurable(self):
        tool = _make_search_tool(max_calls=3)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = _mock_repo_items(1)

        with patch("httpx.get", return_value=mock_resp) as mock_get:
            for _ in range(3):
                tool.call(query="test")
            blocked = tool.call(query="test")

        assert mock_get.call_count == 3
        assert "rate limit" in str(blocked).lower()

    def test_search_repos_http_error(self):
        tool = _make_search_tool()
        response = MagicMock()
        response.status_code = 404
        error = httpx.HTTPStatusError("Not Found", request=MagicMock(), response=response)

        with patch("httpx.get", side_effect=error):
            result = tool.call(query="missing")

        assert "error" in str(result).lower() or "404" in str(result)

    def test_search_repos_network_error(self):
        tool = _make_search_tool()

        with patch("httpx.get", side_effect=httpx.ConnectError("connection refused")):
            result = tool.call(query="test")

        assert isinstance(str(result), str)
        assert len(str(result)) > 0

    def test_no_token_graceful_degradation(self):
        from llamaindex_crew.tools.github_search_tools import GitHubSearchReposTool

        tool = GitHubSearchReposTool(token=None)
        with patch("httpx.get") as mock_get:
            result = tool.call(query="anything")

        mock_get.assert_not_called()
        assert "GitHub search unavailable" in str(result)


class TestGitHubRepoReadmeTool:
    def test_readme_decodes_base64(self):
        tool = _make_readme_tool()
        content = "Hello README"
        encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"content": encoded, "encoding": "base64"}

        with patch("httpx.get", return_value=mock_resp):
            result = tool.call(repo="octocat/Hello-World")

        assert str(result) == content

    def test_readme_truncates_at_4000_chars(self):
        tool = _make_readme_tool()
        content = "x" * 8000
        encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"content": encoded, "encoding": "base64"}

        with patch("httpx.get", return_value=mock_resp):
            result = tool.call(repo="org/big-readme")

        assert len(str(result)) <= 4000

    def test_readme_invalid_owner_repo(self):
        tool = _make_readme_tool()
        with patch("httpx.get") as mock_get:
            result = tool.call(repo="not-a-valid-format")

        mock_get.assert_not_called()
        assert "invalid" in str(result).lower() or "error" in str(result).lower()

    def test_no_token_graceful_degradation(self):
        from llamaindex_crew.tools.github_search_tools import GitHubRepoReadmeTool

        tool = GitHubRepoReadmeTool(token=None)
        with patch("httpx.get") as mock_get:
            result = tool.call(repo="org/repo")

        mock_get.assert_not_called()
        assert "GitHub search unavailable" in str(result)
