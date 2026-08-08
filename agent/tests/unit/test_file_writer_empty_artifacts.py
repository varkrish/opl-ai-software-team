"""Legitimately-empty files must be creatable.

Found by running a real job (2026-08-08): a stdlib-only Python CLI generated
`calculator.py` correctly, but the job was marked FAILED because the agent could
never create `requirements.txt`. The project has no dependencies, so the agent
correctly called ``file_writer(file_path="requirements.txt", content="")`` — and
the stub detector classified the empty body as "unparsed LLM output" and rejected
it on every retry. One unsatisfiable task failed the whole job.

``is_llm_stub_content("") is True`` is correct when judging an LLM *response*;
it is wrong when judging a file whose empty form is a real artifact.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from llamaindex_crew.tools.file_tools import file_writer, set_allowed_file_paths


@pytest.fixture
def workspace(tmp_path):
    set_allowed_file_paths(None, workspace=str(tmp_path))
    return tmp_path


@pytest.mark.parametrize(
    "rel_path",
    ["requirements.txt", "pkg/__init__.py", ".gitkeep", "py.typed"],
)
def test_empty_body_allowed_for_legitimate_artifacts(workspace, rel_path):
    result = file_writer(file_path=rel_path, content="", workspace_path=str(workspace))
    assert result.startswith("✅"), result
    target = workspace / rel_path
    assert target.exists()
    assert target.read_text(encoding="utf-8") == ""


@pytest.mark.parametrize("rel_path", ["app.py", "src/main.js", "README.md"])
def test_empty_body_still_rejected_for_source_files(workspace, rel_path):
    """The guard must keep catching genuinely failed generations."""
    result = file_writer(file_path=rel_path, content="", workspace_path=str(workspace))
    assert result.startswith("❌"), result
    assert not (workspace / rel_path).exists()


def test_meta_commentary_still_rejected_in_exempt_file(workspace):
    """Exemption is for EMPTY bodies only — not for monologue in those files."""
    result = file_writer(
        file_path="requirements.txt",
        content="Thought: I need to create requirements.txt",
        workspace_path=str(workspace),
    )
    assert result.startswith("❌"), result


def test_real_content_still_written(workspace):
    result = file_writer(
        file_path="requirements.txt",
        content="requests==2.32.3\n",
        workspace_path=str(workspace),
    )
    assert result.startswith("✅"), result
    assert (workspace / "requirements.txt").read_text(encoding="utf-8").strip() == "requests==2.32.3"
