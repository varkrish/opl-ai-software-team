"""API tests for dynamic MCP server configuration — including security (env scrubbing, SSRF)."""
import os
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "agent" / "src"))

EXTERNAL_SSE = "https://mcp.example.com/sse"


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_PATH", str(tmp_path / "workspace"))
    monkeypatch.setenv("JOB_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("CREW_TEST_NO_EXECUTOR", "1")
    monkeypatch.setenv("AUTH_ENABLED", "false")
    (tmp_path / "workspace").mkdir(exist_ok=True)

    from crew_studio import asgi_app as asgi_mod
    from crew_studio.asgi_app import app
    from crew_studio.job_database import JobDatabase

    asgi_mod.job_db = JobDatabase(tmp_path / "test.db")
    asgi_mod.base_workspace_path = tmp_path / "workspace"
    return TestClient(app)


# ---------------------------------------------------------------------------
# Basic CRUD
# ---------------------------------------------------------------------------

class TestMcpConfigCrud:
    def test_get_mcp_configs_empty(self, api_client):
        resp = api_client.get("/api/mcp/configs")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_save_and_get_mcp_configs(self, api_client):
        resp = api_client.post("/api/mcp/configs", json={
            "server_name": "test_stdio",
            "target_agent": "global",
            "transport_type": "stdio",
            "command": "python",
            "args": ["-m", "test"],
            "env": {"KEY": "VAL"},
            "tools": ["tool_a", "tool_b"],
        })
        assert resp.status_code == 201
        assert resp.json() == {"saved": True}

        resp = api_client.post("/api/mcp/configs", json={
            "server_name": "test_sse",
            "target_agent": "developer",
            "transport_type": "sse",
            "url": EXTERNAL_SSE,
        })
        assert resp.status_code == 201

        configs = api_client.get("/api/mcp/configs").json()
        assert len(configs) == 2

        sse_cfg = next(c for c in configs if c["server_name"] == "test_sse")
        assert sse_cfg["target_agent"] == "developer"
        assert sse_cfg["url"] == EXTERNAL_SSE

        stdio_cfg = next(c for c in configs if c["server_name"] == "test_stdio")
        assert stdio_cfg["command"] == "python"
        assert stdio_cfg["args"] == ["-m", "test"]
        assert stdio_cfg["env"] == {"KEY": "VAL"}
        assert stdio_cfg["tools"] == ["tool_a", "tool_b"]

    def test_save_mcp_config_invalid_missing_fields(self, api_client):
        resp = api_client.post("/api/mcp/configs", json={
            "server_name": "invalid",
            "transport_type": "stdio",
        })
        assert resp.status_code == 400

        resp = api_client.post("/api/mcp/configs", json={
            "server_name": "invalid",
            "transport_type": "sse",
        })
        assert resp.status_code == 400

    def test_delete_mcp_config(self, api_client):
        api_client.post("/api/mcp/configs", json={
            "server_name": "to_delete",
            "transport_type": "stdio",
            "command": "python",
        })
        resp = api_client.delete("/api/mcp/configs/to_delete")
        assert resp.status_code == 200
        assert resp.json() == {"deleted": True}
        assert len(api_client.get("/api/mcp/configs").json()) == 0

    def test_delete_nonexistent_mcp_config(self, api_client):
        resp = api_client.delete("/api/mcp/configs/does_not_exist")
        assert resp.status_code == 404

    def test_config_for_job_merges_mcp(self, api_client, tmp_path):
        from crew_studio import asgi_app as asgi_mod
        from crew_studio.workflow_config import config_for_job_owner
        from src.llamaindex_crew.config.secure_config import ToolsConfig

        api_client.post("/api/mcp/configs", json={
            "server_name": "global_mcp",
            "target_agent": "global",
            "transport_type": "stdio",
            "command": "python",
        })
        api_client.post("/api/mcp/configs", json={
            "server_name": "dev_mcp",
            "target_agent": "developer",
            "transport_type": "sse",
            "url": EXTERNAL_SSE,
        })

        job_id = "job-mcp-merge-test"
        ws = tempfile.mkdtemp()
        asgi_mod.job_db.create_job(job_id, "Test", ws, owner_id="mock-user-123")

        base_tools = ToolsConfig(global_tools=[], agent_tools={"developer": []})

        class MockConfig:
            def __init__(self):
                self.tools = base_tools
                self.plan_review = None
                self.solutioning = None
                self.generation = None

            def model_copy(self, update=None):
                new_obj = MockConfig()
                if update:
                    for k, v in update.items():
                        setattr(new_obj, k, v)
                return new_obj

        merged = config_for_job_owner(MockConfig(), asgi_mod.job_db, owner_id="mock-user-123")

        globals_list = merged.tools.global_tools
        assert len(globals_list) == 1
        assert globals_list[0].server_name == "global_mcp"

        devs_list = merged.tools.agent_tools["developer"]
        assert len(devs_list) == 1
        assert devs_list[0].server_name == "dev_mcp"
        assert devs_list[0].url == EXTERNAL_SSE


# ---------------------------------------------------------------------------
# Security: env scrubbing
# ---------------------------------------------------------------------------

class TestMcpEnvScrubbing:
    """Tests for _safe_env — verifies secrets are stripped from stdio subprocess env."""

    def _safe_env(self, user_env):
        from src.llamaindex_crew.tools.mcp_bridge import _safe_env
        return _safe_env(user_env)

    def test_llm_api_key_not_inherited(self, monkeypatch):
        monkeypatch.setenv("LLM_API_KEY", "super-secret")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
        safe = self._safe_env({})
        assert "LLM_API_KEY" not in safe
        assert "OPENAI_API_KEY" not in safe

    def test_github_token_not_inherited(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
        safe = self._safe_env({})
        assert "GITHUB_TOKEN" not in safe

    def test_keycloak_vars_not_inherited(self, monkeypatch):
        monkeypatch.setenv("KEYCLOAK_ISSUER_URL", "http://keycloak/realm")
        monkeypatch.setenv("KEYCLOAK_JWKS_URL", "http://keycloak/certs")
        safe = self._safe_env({})
        assert "KEYCLOAK_ISSUER_URL" not in safe
        assert "KEYCLOAK_JWKS_URL" not in safe

    def test_path_is_inherited(self, monkeypatch):
        monkeypatch.setenv("PATH", "/usr/bin:/usr/local/bin")
        safe = self._safe_env({})
        assert "PATH" in safe

    def test_user_supplied_safe_key_passes(self):
        safe = self._safe_env({"MY_CONFIG": "value"})
        assert safe["MY_CONFIG"] == "value"

    def test_user_supplied_secret_key_is_blocked(self):
        safe = self._safe_env({"LLM_API_KEY": "injected"})
        assert "LLM_API_KEY" not in safe

    def test_user_supplied_token_key_is_blocked(self):
        safe = self._safe_env({"MY_TOKEN": "injected"})
        assert "MY_TOKEN" not in safe

    def test_user_supplied_password_key_is_blocked(self):
        safe = self._safe_env({"DB_PASSWORD": "injected"})
        assert "DB_PASSWORD" not in safe


# ---------------------------------------------------------------------------
# Security: SSRF validation on SSE URLs
# ---------------------------------------------------------------------------

class TestMcpSseUrlValidation:
    """Tests for _validate_sse_url and the API-level SSRF rejection."""

    def _validate(self, url):
        from src.llamaindex_crew.tools.mcp_bridge import _validate_sse_url
        _validate_sse_url(url)

    def test_valid_https_url_accepted(self):
        self._validate("https://mcp.example.com/sse")

    def test_valid_http_url_accepted(self):
        self._validate("http://mcp.example.com/sse")

    def test_localhost_rejected(self):
        with pytest.raises(ValueError, match="loopback"):
            self._validate("http://localhost:5000/sse")

    def test_127_rejected(self):
        with pytest.raises(ValueError, match="loopback"):
            self._validate("http://127.0.0.1:5000/sse")

    def test_private_10_rejected(self):
        with pytest.raises(ValueError, match="private"):
            self._validate("http://10.0.0.1/sse")

    def test_private_192_rejected(self):
        with pytest.raises(ValueError, match="private"):
            self._validate("http://192.168.1.1/sse")

    def test_private_172_rejected(self):
        with pytest.raises(ValueError, match="private"):
            self._validate("http://172.16.0.1/sse")

    def test_172_outside_private_range_accepted(self):
        # 172.15.x.x is NOT in the private range
        self._validate("http://172.15.0.1/sse")

    def test_non_http_scheme_rejected(self):
        with pytest.raises(ValueError, match="scheme"):
            self._validate("ftp://example.com/sse")

    def test_file_scheme_rejected(self):
        with pytest.raises(ValueError, match="scheme"):
            self._validate("file:///etc/passwd")

    def test_api_rejects_localhost_sse_url(self, api_client):
        resp = api_client.post("/api/mcp/configs", json={
            "server_name": "bad_sse",
            "transport_type": "sse",
            "url": "http://localhost:9999/sse",
        })
        assert resp.status_code == 400
        assert "loopback" in resp.json()["detail"]

    def test_api_rejects_private_ip_sse_url(self, api_client):
        resp = api_client.post("/api/mcp/configs", json={
            "server_name": "bad_sse",
            "transport_type": "sse",
            "url": "http://10.0.0.1/sse",
        })
        assert resp.status_code == 400
        assert "private" in resp.json()["detail"]

    def test_api_rejects_blocked_env_key(self, api_client):
        resp = api_client.post("/api/mcp/configs", json={
            "server_name": "bad_env",
            "transport_type": "stdio",
            "command": "python",
            "env": {"LLM_API_KEY": "stolen"},
        })
        assert resp.status_code == 400
        assert "LLM_API_KEY" in resp.json()["detail"]
