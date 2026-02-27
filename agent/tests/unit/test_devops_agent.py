"""
Unit tests for DevOpsAgent.
TDD: Written to define the prompt contract and tool binding.
"""
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
import sys

root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "src"))

# Mock heavy LLM dependencies
if "llama_index.llms.ollama" not in sys.modules:
    sys.modules["llama_index.llms.ollama"] = MagicMock()
if "llama_index.embeddings.huggingface" not in sys.modules:
    sys.modules["llama_index.embeddings.huggingface"] = MagicMock()


def _make_agent(cls):
    """Create an agent instance with __init__ bypassed (no LLM needed)."""
    with patch.object(cls, "__init__", lambda self, *a, **kw: None):
        agent = cls(Path("/tmp/ws"), "job-1")
        agent.workspace_path = Path("/tmp/ws")
        agent.project_id = "job-1"
    return agent


# ═════════════════════════════════════════════════════════════════════════════
# build_prompt contract tests
# ═════════════════════════════════════════════════════════════════════════════

class TestDevOpsAgentPrompt:

    def test_build_prompt_includes_tech_stack(self):
        from src.llamaindex_crew.agents.devops_agent import DevOpsAgent
        agent = _make_agent(DevOpsAgent)
        prompt = agent.build_prompt(tech_stack="Python 3.11, FastAPI")
        assert "Python 3.11" in prompt
        assert "FastAPI" in prompt

    def test_build_prompt_includes_containerfile_instruction(self):
        from src.llamaindex_crew.agents.devops_agent import DevOpsAgent
        agent = _make_agent(DevOpsAgent)
        prompt = agent.build_prompt(tech_stack="Node 18")
        assert "Containerfile" in prompt
        assert "write" in prompt.lower() or "create" in prompt.lower()

    def test_build_prompt_includes_pipeline_instruction(self):
        from src.llamaindex_crew.agents.devops_agent import DevOpsAgent
        agent = _make_agent(DevOpsAgent)
        prompt = agent.build_prompt(tech_stack="Go 1.21", pipeline_type="tekton")
        assert "tekton" in prompt.lower() or ".tekton" in prompt or "Pipeline" in prompt

    def test_build_prompt_includes_pipeline_type(self):
        from src.llamaindex_crew.agents.devops_agent import DevOpsAgent
        agent = _make_agent(DevOpsAgent)
        prompt = agent.build_prompt(tech_stack="Python", pipeline_type="tekton")
        assert "tekton" in prompt.lower()
        prompt_gh = agent.build_prompt(tech_stack="Python", pipeline_type="github_actions")
        assert "github" in prompt_gh.lower() or "workflows" in prompt_gh.lower()

    def test_build_prompt_includes_standards_context_when_provided(self):
        from src.llamaindex_crew.agents.devops_agent import DevOpsAgent
        agent = _make_agent(DevOpsAgent)
        prompt = agent.build_prompt(
            tech_stack="Python",
            standards_context="Use UBI9 minimal. No root.",
        )
        assert "UBI9" in prompt
        assert "root" in prompt.lower()

    def test_build_prompt_includes_ubi_and_ocp_standards(self):
        from src.llamaindex_crew.agents.devops_agent import DevOpsAgent
        agent = _make_agent(DevOpsAgent)
        prompt = agent.build_prompt(tech_stack="Python", pipeline_type="tekton")
        assert "UBI" in prompt or "ubi" in prompt
        assert "non-root" in prompt or "root group" in prompt or "privileged" in prompt.lower() or "1024" in prompt

    def test_build_prompt_includes_project_context_when_provided(self):
        from src.llamaindex_crew.agents.devops_agent import DevOpsAgent
        agent = _make_agent(DevOpsAgent)
        prompt = agent.build_prompt(
            tech_stack="Python",
            project_context="Backend only, no frontend.",
        )
        assert "Backend only" in prompt
        assert "no frontend" in prompt

    def test_build_prompt_omits_optional_sections_when_none(self):
        from src.llamaindex_crew.agents.devops_agent import DevOpsAgent
        agent = _make_agent(DevOpsAgent)
        prompt = agent.build_prompt(
            tech_stack="Python",
            standards_context=None,
            project_context=None,
        )
        assert "None" not in prompt
        assert "## Standards" not in prompt or "## Project context" not in prompt or (
            "## Standards\n\n" not in prompt and "## Project context\n\n" not in prompt
        )


class TestDevOpsAgentToolBinding:

    def test_devops_agent_uses_workspace_file_tools(self):
        from src.llamaindex_crew.agents.devops_agent import DevOpsAgent
        with patch("src.llamaindex_crew.agents.devops_agent.BaseLlamaIndexAgent") as MockBase:
            MockBase.return_value = MagicMock()
            agent = DevOpsAgent(Path("/tmp/ws"), "job-1")
            assert MockBase.called
            _, kwargs = MockBase.call_args
            assert "tools" in kwargs
            tool_names = [t.metadata.name for t in kwargs["tools"]]
            assert "file_writer" in tool_names
            assert "file_reader" in tool_names
