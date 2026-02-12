"""
Unit tests for RefinementAgent (mocked LLM/tools where needed).
"""
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add paths
import sys
root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "src"))

# Avoid loading real LLM/embedding stack when importing RefinementAgent (base_agent -> llm_config)
if "llama_index.llms.ollama" not in sys.modules:
    m = MagicMock()
    m.Ollama = MagicMock()
    sys.modules["llama_index.llms.ollama"] = m
if "llama_index.embeddings.huggingface" not in sys.modules:
    m = MagicMock()
    m.HuggingFaceEmbedding = MagicMock()
    sys.modules["llama_index.embeddings.huggingface"] = m


def test_refinement_agent_build_prompt_with_file_path():
    """build_prompt includes target file and initial content when file_path given."""
    from src.llamaindex_crew.agents.refinement_agent import RefinementAgent
    with patch.object(RefinementAgent, "__init__", lambda self, workspace_path, project_id, budget_tracker=None: None):
        agent = RefinementAgent(Path("/tmp/ws"), "job-1")
        agent.workspace_path = Path("/tmp/ws")
        agent.project_id = "job-1"
    prompt = agent.build_prompt(
        user_prompt="Add error handling",
        file_path="src/controller.js",
        initial_file_content="function main() {}",
    )
    assert "Add error handling" in prompt
    assert "src/controller.js" in prompt
    assert "function main() {}" in prompt


def test_refinement_agent_build_prompt_includes_tech_stack_and_history():
    """build_prompt includes tech_stack and refinement_history when provided."""
    from src.llamaindex_crew.agents.refinement_agent import RefinementAgent
    with patch.object(RefinementAgent, "__init__", lambda self, workspace_path, project_id, budget_tracker=None: None):
        agent = RefinementAgent(Path("/tmp/ws"), "job-1")
        agent.workspace_path = Path("/tmp/ws")
        agent.project_id = "job-1"
    prompt = agent.build_prompt(
        user_prompt="Refactor the API",
        tech_stack_content="Node 18, Express",
        refinement_history=[{"prompt": "Add validation", "status": "completed"}],
    )
    assert "Refactor the API" in prompt
    assert "Node 18, Express" in prompt
    assert "Add validation" in prompt or "Previous" in prompt


def test_refinement_agent_build_prompt_file_scope_only_targets_one_file():
    """File-level scope constrains instructions to ONLY the target file."""
    from src.llamaindex_crew.agents.refinement_agent import RefinementAgent
    with patch.object(RefinementAgent, "__init__", lambda self, workspace_path, project_id, budget_tracker=None: None):
        agent = RefinementAgent(Path("/tmp/ws"), "job-1")
        agent.workspace_path = Path("/tmp/ws")
        agent.project_id = "job-1"
    prompt = agent.build_prompt(
        user_prompt="Add comments",
        file_path="src/app.js",
        initial_file_content="const x = 1;",
    )
    assert "src/app.js" in prompt
    assert "ONLY modify" in prompt
    assert "const x = 1;" in prompt
    assert "file_writer" in prompt


def test_refinement_agent_build_prompt_project_wide_fallback():
    """Without file_path, prompt tells agent to discover files via file_lister."""
    from src.llamaindex_crew.agents.refinement_agent import RefinementAgent
    with patch.object(RefinementAgent, "__init__", lambda self, workspace_path, project_id, budget_tracker=None: None):
        agent = RefinementAgent(Path("/tmp/ws"), "job-1")
        agent.workspace_path = Path("/tmp/ws")
        agent.project_id = "job-1"
    prompt = agent.build_prompt(
        user_prompt="Add comments to all files",
    )
    assert "file_lister" in prompt
    assert "file_writer" in prompt


def test_create_workspace_file_tools_returns_four_tools():
    """create_workspace_file_tools returns list of four tools bound to workspace"""
    from src.llamaindex_crew.tools.file_tools import create_workspace_file_tools
    tools = create_workspace_file_tools(Path("/tmp/job-123"))
    assert len(tools) == 4
    names = [t.metadata.name for t in tools]
    assert "file_writer" in names
    assert "file_reader" in names
    assert "file_lister" in names
    assert "file_deleter" in names


def test_file_tools_accept_workspace_path_parameter():
    """file_writer/reader/lister/deleter work with explicit workspace_path (no env)"""
    import tempfile
    from pathlib import Path
    from src.llamaindex_crew.tools.file_tools import file_writer, file_reader, file_lister, file_deleter
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        result = file_writer("test.txt", "hello", workspace_path=str(ws))
        assert "Successfully" in result or "wrote" in result.lower()
        content = file_reader("test.txt", workspace_path=str(ws))
        assert content == "hello"
        listing = file_lister(".", workspace_path=str(ws))
        assert "test.txt" in listing
        del_result = file_deleter("test.txt", workspace_path=str(ws))
        assert "Deleted" in del_result
        assert not (ws / "test.txt").exists()
        assert "not found" in file_deleter("test.txt", workspace_path=str(ws))
