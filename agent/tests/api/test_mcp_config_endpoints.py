"""API tests for dynamic MCP server configuration."""
import json
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "agent" / "src"))


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


class TestMcpConfig:
    def test_get_mcp_configs_empty(self, api_client):
        resp = api_client.get("/api/mcp/configs")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_save_and_get_mcp_configs(self, api_client):
        payload_stdio = {
            "server_name": "test_stdio",
            "target_agent": "global",
            "transport_type": "stdio",
            "command": "python",
            "args": ["-m", "test"],
            "env": {"KEY": "VAL"},
            "tools": ["tool_a", "tool_b"],
        }
        resp = api_client.post("/api/mcp/configs", json=payload_stdio)
        assert resp.status_code == 201
        assert resp.json() == {"saved": True}

        payload_sse = {
            "server_name": "test_sse",
            "target_agent": "developer",
            "transport_type": "sse",
            "url": "http://localhost:5000/sse",
        }
        resp = api_client.post("/api/mcp/configs", json=payload_sse)
        assert resp.status_code == 201

        # Retrieve and verify
        get = api_client.get("/api/mcp/configs")
        assert get.status_code == 200
        configs = get.json()
        assert len(configs) == 2

        # Order is alphabetical by server_name: test_sse first, test_stdio second
        assert configs[0]["server_name"] == "test_sse"
        assert configs[0]["target_agent"] == "developer"
        assert configs[0]["transport_type"] == "sse"
        assert configs[0]["url"] == "http://localhost:5000/sse"

        assert configs[1]["server_name"] == "test_stdio"
        assert configs[1]["target_agent"] == "global"
        assert configs[1]["transport_type"] == "stdio"
        assert configs[1]["command"] == "python"
        assert configs[1]["args"] == ["-m", "test"]
        assert configs[1]["env"] == {"KEY": "VAL"}
        assert configs[1]["tools"] == ["tool_a", "tool_b"]

    def test_save_mcp_config_invalid(self, api_client):
        # Missing command for stdio
        resp = api_client.post("/api/mcp/configs", json={
            "server_name": "invalid",
            "transport_type": "stdio",
        })
        assert resp.status_code == 400

        # Missing url for sse
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
        # Delete
        resp = api_client.delete("/api/mcp/configs/to_delete")
        assert resp.status_code == 200
        assert resp.json() == {"deleted": True}

        # Check list
        get = api_client.get("/api/mcp/configs")
        assert len(get.json()) == 0

    def test_config_for_job_merges_mcp(self, api_client, tmp_path):
        from crew_studio import asgi_app as asgi_mod
        from crew_studio.workflow_config import config_for_job_owner
        from src.llamaindex_crew.config.secure_config import SecretConfig, ToolsConfig

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
            "url": "http://localhost:5000/sse",
        })

        job_id = "job-mcp-merge-test"
        ws = tempfile.mkdtemp()
        asgi_mod.job_db.create_job(job_id, "Test", ws, owner_id="mock-user-123")

        # Mock the SecretConfig base structure
        base_tools = ToolsConfig(global_tools=[], agent_tools={"developer": []})
        
        # Build a mock configuration object
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

        base_cfg = MockConfig()
        merged = config_for_job_owner(base_cfg, asgi_mod.job_db, owner_id="mock-user-123")

        # Verify merge results
        globals_list = merged.tools.global_tools
        developers_list = merged.tools.agent_tools["developer"]

        assert len(globals_list) == 1
        assert globals_list[0].server_name == "global_mcp"
        assert globals_list[0].command == "python"

        assert len(developers_list) == 1
        assert developers_list[0].server_name == "dev_mcp"
        assert developers_list[0].url == "http://localhost:5000/sse"
