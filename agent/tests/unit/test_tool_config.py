"""
Unit tests for SkillsConfig, ToolEntry, ToolsConfig config models.
TDD: Written before implementation to define the config contract.
"""
import pytest
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


class TestSkillsConfig:

    def test_default_service_url_is_none(self):
        from llamaindex_crew.config.secure_config import SkillsConfig
        cfg = SkillsConfig()
        assert cfg.service_url is None

    def test_set_service_url(self):
        from llamaindex_crew.config.secure_config import SkillsConfig
        cfg = SkillsConfig(service_url="http://skills:8090")
        assert cfg.service_url == "http://skills:8090"


class TestNativeToolEntry:

    def test_native_entry_defaults(self):
        from llamaindex_crew.config.secure_config import NativeToolEntry
        entry = NativeToolEntry(module="my.module", name="MyTool")
        assert entry.type == "native"
        assert entry.module == "my.module"
        assert entry.name == "MyTool"
        assert entry.config == {}

    def test_native_entry_with_config(self):
        from llamaindex_crew.config.secure_config import NativeToolEntry
        entry = NativeToolEntry(
            module="llamaindex_crew.tools.skill_tools",
            name="SkillQueryTool",
            config={"service_url": "http://localhost:8090", "default_tags": ["python"]},
        )
        assert entry.config["default_tags"] == ["python"]


class TestMcpToolEntry:

    def test_mcp_entry_stdio(self):
        from llamaindex_crew.config.secure_config import McpToolEntry
        entry = McpToolEntry(
            server_name="jira",
            command="python",
            args=["-m", "crew_jira_connector.server"],
        )
        assert entry.type == "mcp"
        assert entry.server_name == "jira"
        assert entry.command == "python"
        assert entry.args == ["-m", "crew_jira_connector.server"]
        assert entry.url is None
        assert entry.tools == []

    def test_mcp_entry_sse(self):
        from llamaindex_crew.config.secure_config import McpToolEntry
        entry = McpToolEntry(
            server_name="github",
            url="http://github-mcp:3000/sse",
            tools=["create_issue", "list_pull_requests"],
        )
        assert entry.url == "http://github-mcp:3000/sse"
        assert entry.command is None
        assert entry.tools == ["create_issue", "list_pull_requests"]

    def test_mcp_entry_env_vars(self):
        from llamaindex_crew.config.secure_config import McpToolEntry
        entry = McpToolEntry(
            server_name="jira",
            command="python",
            args=[],
            env={"JIRA_URL": "https://jira.example.com"},
        )
        assert entry.env["JIRA_URL"] == "https://jira.example.com"


class TestToolsConfig:

    def test_empty_defaults(self):
        from llamaindex_crew.config.secure_config import ToolsConfig
        cfg = ToolsConfig()
        assert cfg.global_tools == []
        assert cfg.agent_tools == {}

    def test_global_native_tools(self):
        from llamaindex_crew.config.secure_config import ToolsConfig, NativeToolEntry
        cfg = ToolsConfig(
            global_tools=[
                NativeToolEntry(module="my.mod", name="MyTool"),
            ]
        )
        assert len(cfg.global_tools) == 1
        assert cfg.global_tools[0].type == "native"

    def test_agent_tools_mixed(self):
        from llamaindex_crew.config.secure_config import (
            ToolsConfig, NativeToolEntry, McpToolEntry,
        )
        cfg = ToolsConfig(
            agent_tools={
                "developer": [
                    NativeToolEntry(module="m", name="N"),
                    McpToolEntry(server_name="jira", command="python", args=[]),
                ],
            }
        )
        assert len(cfg.agent_tools["developer"]) == 2
        assert cfg.agent_tools["developer"][0].type == "native"
        assert cfg.agent_tools["developer"][1].type == "mcp"


class TestSecretConfigWithTools:

    def test_secret_config_has_skills_and_tools(self):
        from llamaindex_crew.config.secure_config import SecretConfig
        cfg = SecretConfig(
            llm={"api_key": "test-key"},
        )
        assert cfg.skills.service_url is None
        assert cfg.tools.global_tools == []
        assert cfg.tools.agent_tools == {}

    def test_secret_config_with_tools_populated(self):
        from llamaindex_crew.config.secure_config import SecretConfig
        cfg = SecretConfig(
            llm={"api_key": "test-key"},
            skills={"service_url": "http://skills:8090"},
            tools={
                "global_tools": [
                    {"type": "native", "module": "m", "name": "T"},
                ],
                "agent_tools": {
                    "developer": [
                        {"type": "mcp", "server_name": "jira", "command": "python", "args": []},
                    ],
                },
            },
        )
        assert cfg.skills.service_url == "http://skills:8090"
        assert len(cfg.tools.global_tools) == 1
        assert cfg.tools.agent_tools["developer"][0].type == "mcp"

    def test_secret_config_from_yaml_dict(self):
        """Simulates loading from YAML -- raw dicts with 'type' discriminator."""
        from llamaindex_crew.config.secure_config import SecretConfig
        yaml_data = {
            "llm": {"api_key": "k"},
            "tools": {
                "global_tools": [
                    {"type": "native", "module": "a.b", "name": "C"},
                    {"type": "mcp", "server_name": "s", "url": "http://x"},
                ],
            },
        }
        cfg = SecretConfig(**yaml_data)
        assert cfg.tools.global_tools[0].type == "native"
        assert cfg.tools.global_tools[1].type == "mcp"
