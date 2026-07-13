"""Unit tests for the solutioning loop — TDD RED first."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from llamaindex_crew.workflows.solutioning_loop import SolutionResult, run_solutioning_loop


def _critique(approved: bool, **extra):
    payload = {
        "approved": approved,
        "score": 9 if approved else 4,
        "issues": extra.get("issues", []),
        "must_fix": extra.get("must_fix", []),
    }
    return json.dumps(payload)


def _candidates():
    return json.dumps([{"name": "ref", "repo": "org/app", "approach": "Use FastAPI"}])


def _spec_content():
    return "# Solution Specification\n\n" + ("Detailed approach. " * 20)


@pytest.fixture
def mock_config():
    cfg = MagicMock()
    cfg.solutioning.max_passes = 3
    cfg.solutioning.max_github_searches = 10
    cfg.skills.service_url = None
    cfg.tools.global_tools = []
    cfg.tools.agent_tools = {}
    return cfg


class TestSolutioningLoop:
    @patch.object(
        __import__("llamaindex_crew.agents.solution_agents", fromlist=["SolutionCritiqueAgent"]).SolutionCritiqueAgent,
        "__init__",
        lambda self, **kw: None,
    )
    @patch.object(
        __import__("llamaindex_crew.agents.solution_agents", fromlist=["SolutionArchitectAgent"]).SolutionArchitectAgent,
        "__init__",
        lambda self, **kw: None,
    )
    @patch.object(
        __import__("llamaindex_crew.agents.solution_agents", fromlist=["SolutionResearchAgent"]).SolutionResearchAgent,
        "__init__",
        lambda self, **kw: None,
    )
    def test_single_pass_approved(self, tmp_path, mock_config):
        with patch(
            "llamaindex_crew.workflows.solutioning_loop.SolutionResearchAgent.run",
            return_value=_candidates(),
        ), patch(
            "llamaindex_crew.workflows.solutioning_loop.SolutionArchitectAgent.run",
            side_effect=lambda *a, **k: (tmp_path / "solution_spec.md").write_text(
                _spec_content(), encoding="utf-8"
            ) or _spec_content(),
        ), patch(
            "llamaindex_crew.workflows.solutioning_loop.SolutionCritiqueAgent.run",
            return_value=_critique(True),
        ):
            result = run_solutioning_loop(
                vision="Build a calculator",
                project_context="ctx",
                workspace_path=tmp_path,
                config=mock_config,
                budget_tracker=MagicMock(),
                document_indexer=None,
            )

        assert result.approved is True
        assert result.pass_count == 1
        assert (tmp_path / "solution_candidates.json").exists()
        assert (tmp_path / "solution_spec.md").exists()
        assert (tmp_path / "solution_critique_pass_1.json").exists()

    @patch.object(
        __import__("llamaindex_crew.agents.solution_agents", fromlist=["SolutionCritiqueAgent"]).SolutionCritiqueAgent,
        "__init__",
        lambda self, **kw: None,
    )
    @patch.object(
        __import__("llamaindex_crew.agents.solution_agents", fromlist=["SolutionArchitectAgent"]).SolutionArchitectAgent,
        "__init__",
        lambda self, **kw: None,
    )
    @patch.object(
        __import__("llamaindex_crew.agents.solution_agents", fromlist=["SolutionResearchAgent"]).SolutionResearchAgent,
        "__init__",
        lambda self, **kw: None,
    )
    def test_critique_rejection_triggers_rerun(self, tmp_path, mock_config):
        critique_side_effect = [
            _critique(False, must_fix=["missing auth"]),
            _critique(True),
        ]
        with patch(
            "llamaindex_crew.workflows.solutioning_loop.SolutionResearchAgent.run",
            return_value=_candidates(),
        ) as research_run, patch(
            "llamaindex_crew.workflows.solutioning_loop.SolutionArchitectAgent.run",
            side_effect=lambda *a, **k: (tmp_path / "solution_spec.md").write_text(
                _spec_content(), encoding="utf-8"
            ) or _spec_content(),
        ) as architect_run, patch(
            "llamaindex_crew.workflows.solutioning_loop.SolutionCritiqueAgent.run",
            side_effect=critique_side_effect,
        ):
            result = run_solutioning_loop(
                vision="Build auth service",
                project_context="ctx",
                workspace_path=tmp_path,
                config=mock_config,
                budget_tracker=MagicMock(),
                document_indexer=None,
            )

        assert result.approved is True
        assert result.pass_count == 2
        research_run.assert_called_once()
        assert architect_run.call_count == 2

    @patch.object(
        __import__("llamaindex_crew.agents.solution_agents", fromlist=["SolutionCritiqueAgent"]).SolutionCritiqueAgent,
        "__init__",
        lambda self, **kw: None,
    )
    @patch.object(
        __import__("llamaindex_crew.agents.solution_agents", fromlist=["SolutionArchitectAgent"]).SolutionArchitectAgent,
        "__init__",
        lambda self, **kw: None,
    )
    @patch.object(
        __import__("llamaindex_crew.agents.solution_agents", fromlist=["SolutionResearchAgent"]).SolutionResearchAgent,
        "__init__",
        lambda self, **kw: None,
    )
    def test_approved_with_must_fix_triggers_rerun(self, tmp_path, mock_config):
        """LLM may return approved=true with must_fix — loop must not stop on pass 1."""
        critique_side_effect = [
            _critique(True, must_fix=["Add Podman --label flag"]),
            _critique(True),
        ]
        with patch(
            "llamaindex_crew.workflows.solutioning_loop.SolutionResearchAgent.run",
            return_value=_candidates(),
        ), patch(
            "llamaindex_crew.workflows.solutioning_loop.SolutionArchitectAgent.run",
            side_effect=lambda *a, **k: (tmp_path / "solution_spec.md").write_text(
                _spec_content(), encoding="utf-8"
            ) or _spec_content(),
        ) as architect_run, patch(
            "llamaindex_crew.workflows.solutioning_loop.SolutionCritiqueAgent.run",
            side_effect=critique_side_effect,
        ):
            result = run_solutioning_loop(
                vision="Build sandbox API",
                project_context="ctx",
                workspace_path=tmp_path,
                config=mock_config,
                budget_tracker=MagicMock(),
                document_indexer=None,
            )

        assert result.approved is True
        assert result.pass_count == 2
        assert architect_run.call_count == 2
        pass1 = json.loads((tmp_path / "solution_critique_pass_1.json").read_text())
        assert pass1["approved"] is False

    @patch.object(
        __import__("llamaindex_crew.agents.solution_agents", fromlist=["SolutionCritiqueAgent"]).SolutionCritiqueAgent,
        "__init__",
        lambda self, **kw: None,
    )
    @patch.object(
        __import__("llamaindex_crew.agents.solution_agents", fromlist=["SolutionArchitectAgent"]).SolutionArchitectAgent,
        "__init__",
        lambda self, **kw: None,
    )
    @patch.object(
        __import__("llamaindex_crew.agents.solution_agents", fromlist=["SolutionResearchAgent"]).SolutionResearchAgent,
        "__init__",
        lambda self, **kw: None,
    )
    def test_max_passes_hard_cap(self, tmp_path, mock_config):
        with patch(
            "llamaindex_crew.workflows.solutioning_loop.SolutionResearchAgent.run",
            return_value=_candidates(),
        ), patch(
            "llamaindex_crew.workflows.solutioning_loop.SolutionArchitectAgent.run",
            side_effect=lambda *a, **k: (tmp_path / "solution_spec.md").write_text(
                _spec_content(), encoding="utf-8"
            ) or _spec_content(),
        ) as architect_run, patch(
            "llamaindex_crew.workflows.solutioning_loop.SolutionCritiqueAgent.run",
            return_value=_critique(False),
        ):
            result = run_solutioning_loop(
                vision="Build app",
                project_context="ctx",
                workspace_path=tmp_path,
                config=mock_config,
                budget_tracker=MagicMock(),
                document_indexer=None,
                max_passes=3,
            )

        assert result.approved is False
        assert result.pass_count == 3
        assert architect_run.call_count == 3

    @patch.object(
        __import__("llamaindex_crew.agents.solution_agents", fromlist=["SolutionCritiqueAgent"]).SolutionCritiqueAgent,
        "__init__",
        lambda self, **kw: None,
    )
    @patch.object(
        __import__("llamaindex_crew.agents.solution_agents", fromlist=["SolutionArchitectAgent"]).SolutionArchitectAgent,
        "__init__",
        lambda self, **kw: None,
    )
    @patch.object(
        __import__("llamaindex_crew.agents.solution_agents", fromlist=["SolutionResearchAgent"]).SolutionResearchAgent,
        "__init__",
        lambda self, **kw: None,
    )
    def test_max_passes_configurable(self, tmp_path, mock_config):
        with patch(
            "llamaindex_crew.workflows.solutioning_loop.SolutionResearchAgent.run",
            return_value=_candidates(),
        ), patch(
            "llamaindex_crew.workflows.solutioning_loop.SolutionArchitectAgent.run",
            side_effect=lambda *a, **k: (tmp_path / "solution_spec.md").write_text(
                _spec_content(), encoding="utf-8"
            ) or _spec_content(),
        ) as architect_run, patch(
            "llamaindex_crew.workflows.solutioning_loop.SolutionCritiqueAgent.run",
            return_value=_critique(False),
        ):
            result = run_solutioning_loop(
                vision="Build app",
                project_context="ctx",
                workspace_path=tmp_path,
                config=mock_config,
                budget_tracker=MagicMock(),
                document_indexer=None,
                max_passes=2,
            )

        assert result.pass_count == 2
        assert architect_run.call_count == 2

    @patch.object(
        __import__("llamaindex_crew.agents.solution_agents", fromlist=["SolutionCritiqueAgent"]).SolutionCritiqueAgent,
        "__init__",
        lambda self, **kw: None,
    )
    @patch.object(
        __import__("llamaindex_crew.agents.solution_agents", fromlist=["SolutionArchitectAgent"]).SolutionArchitectAgent,
        "__init__",
        lambda self, **kw: None,
    )
    @patch.object(
        __import__("llamaindex_crew.agents.solution_agents", fromlist=["SolutionResearchAgent"]).SolutionResearchAgent,
        "__init__",
        lambda self, **kw: None,
    )
    def test_artifacts_persisted_per_pass(self, tmp_path, mock_config):
        with patch(
            "llamaindex_crew.workflows.solutioning_loop.SolutionResearchAgent.run",
            return_value=_candidates(),
        ), patch(
            "llamaindex_crew.workflows.solutioning_loop.SolutionArchitectAgent.run",
            side_effect=lambda *a, **k: (tmp_path / "solution_spec.md").write_text(
                _spec_content(), encoding="utf-8"
            ) or _spec_content(),
        ), patch(
            "llamaindex_crew.workflows.solutioning_loop.SolutionCritiqueAgent.run",
            return_value=_critique(False),
        ):
            run_solutioning_loop(
                vision="Build app",
                project_context="ctx",
                workspace_path=tmp_path,
                config=mock_config,
                budget_tracker=MagicMock(),
                document_indexer=None,
                max_passes=3,
            )

        for n in (1, 2, 3):
            assert (tmp_path / f"solution_critique_pass_{n}.json").exists()

    @patch.object(
        __import__("llamaindex_crew.agents.solution_agents", fromlist=["SolutionCritiqueAgent"]).SolutionCritiqueAgent,
        "__init__",
        lambda self, **kw: None,
    )
    @patch.object(
        __import__("llamaindex_crew.agents.solution_agents", fromlist=["SolutionArchitectAgent"]).SolutionArchitectAgent,
        "__init__",
        lambda self, **kw: None,
    )
    @patch.object(
        __import__("llamaindex_crew.agents.solution_agents", fromlist=["SolutionResearchAgent"]).SolutionResearchAgent,
        "__init__",
        lambda self, **kw: None,
    )
    def test_spec_archived_per_pass_with_distinct_content(self, tmp_path, mock_config):
        """Each pass's solution_spec.md must be archived before the next pass
        overwrites it, so revisions can be diffed pass-over-pass."""
        revisions = [
            "# Solution Specification\n\n" + ("Draft one. " * 20),
            "# Solution Specification\n\n" + ("Revised draft two. " * 20),
            "# Solution Specification\n\n" + ("Final draft three. " * 20),
        ]

        def fake_architect_run(*a, **k):
            content = revisions[fake_architect_run.calls]
            fake_architect_run.calls += 1
            (tmp_path / "solution_spec.md").write_text(content, encoding="utf-8")
            return content

        fake_architect_run.calls = 0

        with patch(
            "llamaindex_crew.workflows.solutioning_loop.SolutionResearchAgent.run",
            return_value=_candidates(),
        ), patch(
            "llamaindex_crew.workflows.solutioning_loop.SolutionArchitectAgent.run",
            side_effect=fake_architect_run,
        ), patch(
            "llamaindex_crew.workflows.solutioning_loop.SolutionCritiqueAgent.run",
            return_value=_critique(False),
        ):
            run_solutioning_loop(
                vision="Build app",
                project_context="ctx",
                workspace_path=tmp_path,
                config=mock_config,
                budget_tracker=MagicMock(),
                document_indexer=None,
                max_passes=3,
            )

        for n, expected_content in enumerate(revisions, start=1):
            pass_file = tmp_path / f"solution_spec_pass_{n}.md"
            assert pass_file.exists()
            assert pass_file.read_text(encoding="utf-8") == expected_content

        # Confirm the archived passes actually differ from each other —
        # otherwise archiving wouldn't reveal whether revisions occurred.
        contents = [
            (tmp_path / f"solution_spec_pass_{n}.md").read_text(encoding="utf-8")
            for n in (1, 2, 3)
        ]
        assert len(set(contents)) == 3

    @patch.object(
        __import__("llamaindex_crew.agents.solution_agents", fromlist=["SolutionCritiqueAgent"]).SolutionCritiqueAgent,
        "__init__",
        lambda self, **kw: None,
    )
    @patch.object(
        __import__("llamaindex_crew.agents.solution_agents", fromlist=["SolutionArchitectAgent"]).SolutionArchitectAgent,
        "__init__",
        lambda self, **kw: None,
    )
    @patch.object(
        __import__("llamaindex_crew.agents.solution_agents", fromlist=["SolutionResearchAgent"]).SolutionResearchAgent,
        "__init__",
        lambda self, **kw: None,
    )
    def test_candidates_json_valid(self, tmp_path, mock_config):
        with patch(
            "llamaindex_crew.workflows.solutioning_loop.SolutionResearchAgent.run",
            return_value=_candidates(),
        ), patch(
            "llamaindex_crew.workflows.solutioning_loop.SolutionArchitectAgent.run",
            return_value=_spec_content(),
        ), patch(
            "llamaindex_crew.workflows.solutioning_loop.SolutionCritiqueAgent.run",
            return_value=_critique(True),
        ):
            run_solutioning_loop(
                vision="Build app",
                project_context="ctx",
                workspace_path=tmp_path,
                config=mock_config,
                budget_tracker=MagicMock(),
                document_indexer=None,
            )

        data = json.loads((tmp_path / "solution_candidates.json").read_text(encoding="utf-8"))
        assert isinstance(data, list)

    @patch.object(
        __import__("llamaindex_crew.agents.solution_agents", fromlist=["SolutionCritiqueAgent"]).SolutionCritiqueAgent,
        "__init__",
        lambda self, **kw: None,
    )
    @patch.object(
        __import__("llamaindex_crew.agents.solution_agents", fromlist=["SolutionArchitectAgent"]).SolutionArchitectAgent,
        "__init__",
        lambda self, **kw: None,
    )
    @patch.object(
        __import__("llamaindex_crew.agents.solution_agents", fromlist=["SolutionResearchAgent"]).SolutionResearchAgent,
        "__init__",
        lambda self, **kw: None,
    )
    def test_solution_spec_md_nonempty(self, tmp_path, mock_config):
        with patch(
            "llamaindex_crew.workflows.solutioning_loop.SolutionResearchAgent.run",
            return_value=_candidates(),
        ), patch(
            "llamaindex_crew.workflows.solutioning_loop.SolutionArchitectAgent.run",
            side_effect=lambda *a, **k: (tmp_path / "solution_spec.md").write_text(
                _spec_content(), encoding="utf-8"
            ) or _spec_content(),
        ), patch(
            "llamaindex_crew.workflows.solutioning_loop.SolutionCritiqueAgent.run",
            return_value=_critique(True),
        ):
            run_solutioning_loop(
                vision="Build app",
                project_context="ctx",
                workspace_path=tmp_path,
                config=mock_config,
                budget_tracker=MagicMock(),
                document_indexer=None,
            )

        content = (tmp_path / "solution_spec.md").read_text(encoding="utf-8")
        assert len(content) > 100

    @patch.object(
        __import__("llamaindex_crew.agents.solution_agents", fromlist=["SolutionCritiqueAgent"]).SolutionCritiqueAgent,
        "__init__",
        lambda self, **kw: None,
    )
    @patch.object(
        __import__("llamaindex_crew.agents.solution_agents", fromlist=["SolutionArchitectAgent"]).SolutionArchitectAgent,
        "__init__",
        lambda self, **kw: None,
    )
    @patch.object(
        __import__("llamaindex_crew.agents.solution_agents", fromlist=["SolutionResearchAgent"]).SolutionResearchAgent,
        "__init__",
        lambda self, **kw: None,
    )
    def test_critique_history_in_result(self, tmp_path, mock_config):
        with patch(
            "llamaindex_crew.workflows.solutioning_loop.SolutionResearchAgent.run",
            return_value=_candidates(),
        ), patch(
            "llamaindex_crew.workflows.solutioning_loop.SolutionArchitectAgent.run",
            side_effect=lambda *a, **k: (tmp_path / "solution_spec.md").write_text(
                _spec_content(), encoding="utf-8"
            ) or _spec_content(),
        ), patch(
            "llamaindex_crew.workflows.solutioning_loop.SolutionCritiqueAgent.run",
            side_effect=[_critique(False), _critique(True)],
        ):
            result = run_solutioning_loop(
                vision="Build app",
                project_context="ctx",
                workspace_path=tmp_path,
                config=mock_config,
                budget_tracker=MagicMock(),
                document_indexer=None,
            )

        assert len(result.critique_history) == 2
        for entry in result.critique_history:
            assert "approved" in entry
            assert "score" in entry
            assert "issues" in entry

    @patch.object(
        __import__("llamaindex_crew.agents.solution_agents", fromlist=["SolutionCritiqueAgent"]).SolutionCritiqueAgent,
        "__init__",
        lambda self, **kw: None,
    )
    @patch.object(
        __import__("llamaindex_crew.agents.solution_agents", fromlist=["SolutionArchitectAgent"]).SolutionArchitectAgent,
        "__init__",
        lambda self, **kw: None,
    )
    @patch.object(
        __import__("llamaindex_crew.agents.solution_agents", fromlist=["SolutionResearchAgent"]).SolutionResearchAgent,
        "__init__",
        lambda self, **kw: None,
    )
    def test_progress_callback_called(self, tmp_path, mock_config):
        cb = MagicMock()
        with patch(
            "llamaindex_crew.workflows.solutioning_loop.SolutionResearchAgent.run",
            return_value=_candidates(),
        ), patch(
            "llamaindex_crew.workflows.solutioning_loop.SolutionArchitectAgent.run",
            return_value=_spec_content(),
        ), patch(
            "llamaindex_crew.workflows.solutioning_loop.SolutionCritiqueAgent.run",
            return_value=_critique(True),
        ):
            run_solutioning_loop(
                vision="Build app",
                project_context="ctx",
                workspace_path=tmp_path,
                config=mock_config,
                budget_tracker=MagicMock(),
                document_indexer=None,
                progress_callback=cb,
            )

        assert cb.call_count >= 3

    @patch.object(
        __import__("llamaindex_crew.agents.solution_agents", fromlist=["SolutionCritiqueAgent"]).SolutionCritiqueAgent,
        "__init__",
        lambda self, **kw: None,
    )
    @patch.object(
        __import__("llamaindex_crew.agents.solution_agents", fromlist=["SolutionArchitectAgent"]).SolutionArchitectAgent,
        "__init__",
        lambda self, **kw: None,
    )
    @patch.object(
        __import__("llamaindex_crew.agents.solution_agents", fromlist=["SolutionResearchAgent"]).SolutionResearchAgent,
        "__init__",
        lambda self, **kw: None,
    )
    def test_result_dataclass_fields(self, tmp_path, mock_config):
        with patch(
            "llamaindex_crew.workflows.solutioning_loop.SolutionResearchAgent.run",
            return_value=_candidates(),
        ), patch(
            "llamaindex_crew.workflows.solutioning_loop.SolutionArchitectAgent.run",
            return_value=_spec_content(),
        ), patch(
            "llamaindex_crew.workflows.solutioning_loop.SolutionCritiqueAgent.run",
            return_value=_critique(True),
        ):
            result = run_solutioning_loop(
                vision="Build app",
                project_context="ctx",
                workspace_path=tmp_path,
                config=mock_config,
                budget_tracker=MagicMock(),
                document_indexer=None,
            )

        assert isinstance(result, SolutionResult)
        assert isinstance(result.approved, bool)
        assert isinstance(result.pass_count, int)
        assert isinstance(result.spec_path, Path)
        assert isinstance(result.candidates_path, Path)
        assert isinstance(result.critique_history, list)
