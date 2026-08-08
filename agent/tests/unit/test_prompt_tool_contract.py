"""Contract tests: every tool an agent's prompt tells it to call must actually exist.

Regression guard for the 2026-07-14 breakage (commit 6b1ddae), where
``replace_file_content`` was removed from the tool registry while every prompt
still ordered the agent to call it. The refinement agent then produced no file
writes at all, and the runner reported "the agent completed but did not modify".

These tests assert against the REAL tool registry rather than prompt substrings,
so a future rename of an edit primitive fails here instead of in production.
"""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from llamaindex_crew.tools.file_tools import create_workspace_file_tools


# Tool names that appear in prompts and must resolve to a registered tool.
# Anything matching this pattern in prompt text is checked against the registry.
_CANDIDATE_TOOL_PATTERN = re.compile(
    r"\b(file_reader|file_writer|file_lister|file_deleter|bulk_file_writer|"
    r"patch_file_content|replace_file_content|code_search|code_structure|"
    r"code_context|code_impact)\b"
)

# Tools provided by append_tldr_tools rather than create_workspace_file_tools.
_TLDR_TOOL_NAMES = {"code_search", "code_structure", "code_context", "code_impact"}


def _workspace_tool_names(tmp_path: Path) -> set:
    return {t.metadata.name for t in create_workspace_file_tools(tmp_path)}


def test_replace_file_content_is_gone(tmp_path):
    """patch_file_content is the single edit primitive; the old one must stay removed."""
    names = _workspace_tool_names(tmp_path)
    assert "patch_file_content" in names
    assert "replace_file_content" not in names


def test_refinement_agent_prompt_only_names_real_tools(tmp_path):
    """Every tool named in the RefinementAgent system prompt must be registered."""
    from llamaindex_crew.agents.refinement_agent import REFINEMENT_SYSTEM_FALLBACK

    available = _workspace_tool_names(tmp_path) | _TLDR_TOOL_NAMES
    named = set(_CANDIDATE_TOOL_PATTERN.findall(REFINEMENT_SYSTEM_FALLBACK))
    missing = named - available
    assert not missing, f"System prompt names nonexistent tools: {sorted(missing)}"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"user_prompt": "Add error handling", "file_path": "src/app.py",
         "initial_file_content": "def main():\n    pass\n", "scope": "file"},
        {"user_prompt": "Add error handling", "file_path": "src/app.py",
         "initial_file_content": "x = 1\n" * 5000, "scope": "impact",
         "allowed_files": ["src/app.py", "src/other.py"]},
        {"user_prompt": "Remove the ff_* tools", "file_listing": "src/app.py\n"},
    ],
    ids=["file-scope", "impact-scope-large-file", "project-scope"],
)
def test_built_prompts_only_name_real_tools(tmp_path, kwargs):
    """build_prompt output, across every scope branch, must not name missing tools."""
    from llamaindex_crew.agents.refinement_agent import RefinementAgent

    agent = RefinementAgent.__new__(RefinementAgent)  # skip LLM construction
    agent.workspace_path = tmp_path
    agent.project_id = "job-1"

    prompt = RefinementAgent.build_prompt(agent, **kwargs)

    available = _workspace_tool_names(tmp_path) | _TLDR_TOOL_NAMES
    missing = set(_CANDIDATE_TOOL_PATTERN.findall(prompt)) - available
    assert not missing, f"build_prompt names nonexistent tools: {sorted(missing)}"


def test_batched_prompt_only_names_real_tools(tmp_path):
    """The project-wide batched-edit prompt must not name missing tools."""
    from llamaindex_crew.agents.refinement_agent import RefinementAgent

    agent = RefinementAgent.__new__(RefinementAgent)
    agent.workspace_path = tmp_path
    agent.project_id = "job-1"

    prompt = RefinementAgent._build_batched_prompt(
        agent,
        user_prompt="Rename greet to hello",
        candidate_files={"a.py": "def greet(): pass\n", "b.py": "greet()\n"},
    )

    available = _workspace_tool_names(tmp_path) | _TLDR_TOOL_NAMES
    missing = set(_CANDIDATE_TOOL_PATTERN.findall(prompt)) - available
    assert not missing, f"Batched prompt names nonexistent tools: {sorted(missing)}"


def test_patch_file_content_applies_a_search_replace_block(tmp_path):
    """End-to-end proof the documented SEARCH/REPLACE format actually writes to disk."""
    from llamaindex_crew.tools.file_tools import patch_file_content, set_allowed_file_paths

    set_allowed_file_paths(None, workspace=str(tmp_path))
    target = tmp_path / "app.py"
    target.write_text('def greet(name):\n    print("hi")\n', encoding="utf-8")

    result = patch_file_content(
        file_path="app.py",
        diff_blocks=(
            "<<<<<<< SEARCH\n"
            '    print("hi")\n'
            "=======\n"
            '    logger.info("hi %s", name)\n'
            ">>>>>>> REPLACE\n"
        ),
        workspace_path=str(tmp_path),
    )

    assert result.startswith("✅"), result
    assert 'logger.info("hi %s", name)' in target.read_text(encoding="utf-8")


def test_patch_deletes_code_with_empty_replace_section(tmp_path):
    """The 'delete code' idiom the prompts teach must actually work."""
    from llamaindex_crew.tools.file_tools import patch_file_content, set_allowed_file_paths

    set_allowed_file_paths(None, workspace=str(tmp_path))
    target = tmp_path / "app.py"
    target.write_text("keep_me()\ndelete_me()\nkeep_me_too()\n", encoding="utf-8")

    result = patch_file_content(
        file_path="app.py",
        diff_blocks="<<<<<<< SEARCH\ndelete_me()\n=======\n>>>>>>> REPLACE\n",
        workspace_path=str(tmp_path),
    )

    assert result.startswith("✅"), result
    content = target.read_text(encoding="utf-8")
    assert "delete_me()" not in content
    assert "keep_me()" in content and "keep_me_too()" in content
