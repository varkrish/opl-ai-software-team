"""Regression tests for SolutionArchitectAgent.run() spec-file persistence.

Covers a bug where revision passes silently discarded the architect's
(re)generated response because the fallback write only fired when
solution_spec.md did not already exist.
"""
from pathlib import Path
from unittest.mock import MagicMock

from llamaindex_crew.agents.solution_agents import SolutionArchitectAgent


def _make_agent(tmp_path: Path, chat_return: str) -> SolutionArchitectAgent:
    agent = SolutionArchitectAgent.__new__(SolutionArchitectAgent)
    agent.workspace_path = tmp_path
    agent.config = None
    agent.agent = MagicMock()
    agent.agent.chat.return_value = chat_return
    return agent


class TestSolutionArchitectAgentSpecPersistence:
    def test_first_pass_writes_spec_when_no_tool_write_occurs(self, tmp_path):
        agent = _make_agent(tmp_path, "# Solution Specification\n\nFirst draft.")

        agent.run("Build app", "ctx", "[]")

        spec_path = tmp_path / "solution_spec.md"
        assert spec_path.exists()
        assert "First draft." in spec_path.read_text(encoding="utf-8")

    def test_revision_pass_overwrites_stale_spec_with_new_response(self, tmp_path):
        spec_path = tmp_path / "solution_spec.md"
        spec_path.write_text("# Solution Specification\n\nStale draft.", encoding="utf-8")

        agent = _make_agent(tmp_path, "# Solution Specification\n\nRevised draft addressing feedback.")
        agent.run("Build app", "ctx", "[]", feedback="Add persistence details")

        content = spec_path.read_text(encoding="utf-8")
        assert "Revised draft addressing feedback." in content
        assert "Stale draft." not in content

    def test_does_not_clobber_content_written_via_tool_call_during_chat(self, tmp_path):
        spec_path = tmp_path / "solution_spec.md"
        spec_path.write_text("# Solution Specification\n\nOld draft.", encoding="utf-8")

        agent = _make_agent(tmp_path, "Sure, I've written the file.")

        def fake_chat(message, **kwargs):
            # Simulate the architect using its file_writer tool during chat(),
            # which updates the file's mtime before run() returns.
            spec_path.write_text(
                "# Solution Specification\n\nWritten via tool call.", encoding="utf-8"
            )
            return "Sure, I've written the file."

        agent.agent.chat.side_effect = fake_chat

        agent.run("Build app", "ctx", "[]", feedback="Add persistence details")

        content = spec_path.read_text(encoding="utf-8")
        assert "Written via tool call." in content
        assert "Sure, I've written the file." not in content
