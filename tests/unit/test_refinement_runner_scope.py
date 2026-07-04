"""
TDD tests for refinement scope routing in crew_studio/refinement_runner.py.

Bug: when a file was selected in the UI and the user picked "Whole project"
scope, the presence of `file_path` caused the dispatcher to fall through to
single-file refinement, silently downgrading "project" scope to "file" scope.
`scope == "project"` must always route to project-wide refinement, regardless
of whether `file_path` happens to be set — with `file_path` passed through
only as a hint so the previously-open file is prioritized among candidates.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "agent"))
sys.path.insert(0, str(root / "agent" / "src"))

from crew_studio import refinement_runner


def _common_patches():
    """Patch everything _run_refinement_impl touches before it dispatches on scope."""
    return [
        patch.object(refinement_runner, "_pull_and_reindex"),
        patch.object(refinement_runner, "_git_snapshot"),
        patch.object(refinement_runner, "_load_tech_stack", return_value=""),
        patch.object(refinement_runner, "_load_file_listing", return_value=""),
        patch.object(refinement_runner, "_load_project_context", return_value=""),
        patch.object(refinement_runner, "_job_metadata", return_value={}),
    ]


def _run_with_patches(patches, fn):
    for p in patches:
        p.start()
    try:
        return fn()
    finally:
        for p in patches:
            p.stop()


def _call(job_db, file_path=None, scope=None, tmp_path=None):
    return refinement_runner._run_refinement_impl(
        job_id="job-1",
        workspace_path=tmp_path,
        prompt="do the thing",
        refinement_id="ref-1",
        job_db=job_db,
        progress_callback=MagicMock(),
        file_path=file_path,
        scope=scope,
    )


class TestScopeRouting:

    def test_project_scope_routes_to_project_wide_even_with_file_open(self, tmp_path):
        """The exact reported bug: file selected in UI + 'Whole project' scope
        must NOT be routed to single-file refinement."""
        job_db = MagicMock()
        job_db.get_refinement_history.return_value = []

        with patch.object(refinement_runner, "_run_project_wide_refinement",
                           return_value={"status": "success"}) as mock_project, \
             patch.object(refinement_runner, "_run_single_file_refinement") as mock_file, \
             patch.object(refinement_runner, "_run_impact_refinement") as mock_impact:
            _run_with_patches(_common_patches(), lambda: _call(
                job_db, file_path="src/foo.py", scope="project", tmp_path=tmp_path,
            ))

        mock_project.assert_called_once()
        mock_file.assert_not_called()
        mock_impact.assert_not_called()
        assert mock_project.call_args.kwargs.get("hint_file_path") == "src/foo.py"

    def test_file_scope_routes_to_single_file(self, tmp_path):
        job_db = MagicMock()
        job_db.get_refinement_history.return_value = []

        with patch.object(refinement_runner, "_run_project_wide_refinement") as mock_project, \
             patch.object(refinement_runner, "_run_single_file_refinement",
                           return_value={"status": "success"}) as mock_file, \
             patch.object(refinement_runner, "_run_impact_refinement") as mock_impact:
            _run_with_patches(_common_patches(), lambda: _call(
                job_db, file_path="src/foo.py", scope="file", tmp_path=tmp_path,
            ))

        mock_file.assert_called_once()
        assert mock_file.call_args.kwargs.get("scope") == "file"
        mock_project.assert_not_called()
        mock_impact.assert_not_called()

    def test_impact_scope_routes_to_impact_refinement(self, tmp_path):
        job_db = MagicMock()
        job_db.get_refinement_history.return_value = []

        with patch.object(refinement_runner, "_run_project_wide_refinement") as mock_project, \
             patch.object(refinement_runner, "_run_single_file_refinement") as mock_file, \
             patch.object(refinement_runner, "_run_impact_refinement",
                           return_value={"status": "success"}) as mock_impact:
            _run_with_patches(_common_patches(), lambda: _call(
                job_db, file_path="src/foo.py", scope="impact", tmp_path=tmp_path,
            ))

        mock_impact.assert_called_once()
        mock_project.assert_not_called()
        mock_file.assert_not_called()

    def test_no_file_path_defaults_to_project_wide(self, tmp_path):
        job_db = MagicMock()
        job_db.get_refinement_history.return_value = []

        with patch.object(refinement_runner, "_run_project_wide_refinement",
                           return_value={"status": "success"}) as mock_project, \
             patch.object(refinement_runner, "_run_single_file_refinement") as mock_file, \
             patch.object(refinement_runner, "_run_impact_refinement") as mock_impact:
            _run_with_patches(_common_patches(), lambda: _call(
                job_db, file_path=None, scope=None, tmp_path=tmp_path,
            ))

        mock_project.assert_called_once()
        assert mock_project.call_args.kwargs.get("hint_file_path") is None
        mock_file.assert_not_called()
        mock_impact.assert_not_called()

    def test_file_path_without_scope_defaults_to_impact(self, tmp_path):
        """Backward compatibility: omitting scope with a file_path keeps the
        existing default behavior (impact scope)."""
        job_db = MagicMock()
        job_db.get_refinement_history.return_value = []

        with patch.object(refinement_runner, "_run_project_wide_refinement") as mock_project, \
             patch.object(refinement_runner, "_run_single_file_refinement") as mock_file, \
             patch.object(refinement_runner, "_run_impact_refinement",
                           return_value={"status": "success"}) as mock_impact:
            _run_with_patches(_common_patches(), lambda: _call(
                job_db, file_path="src/foo.py", scope=None, tmp_path=tmp_path,
            ))

        mock_impact.assert_called_once()
        mock_project.assert_not_called()
        mock_file.assert_not_called()


class TestProjectWideHintFile:

    def test_hint_file_prioritized_in_candidates(self, tmp_path):
        """hint_file_path must be included among candidates even if the prompt
        token filter wouldn't otherwise have selected it."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "unrelated.py").write_text("print('noop')")
        (tmp_path / "src" / "opened_file.py").write_text("print('also unrelated')")

        job_db = MagicMock()

        with patch.object(refinement_runner, "_discover_source_files",
                           return_value=["src/unrelated.py", "src/opened_file.py"]), \
             patch.object(refinement_runner, "_candidate_files_from_prompt",
                           return_value=["src/unrelated.py"]), \
             patch.object(refinement_runner, "_preload_source_files",
                           return_value={"src/unrelated.py": "x", "src/opened_file.py": "y"}), \
             patch.object(refinement_runner, "_workspace_has_changes", return_value=True), \
             patch.object(refinement_runner, "_post_fix_gates"), \
             patch.object(refinement_runner, "_complete_refinement",
                           return_value={"status": "success"}), \
             patch("src.llamaindex_crew.agents.refinement_agent.RefinementAgent") as MockAgent:
            MockAgent.return_value.run.return_value = None
            refinement_runner._run_project_wide_refinement(
                job_id="job-1",
                workspace_path=tmp_path,
                prompt="tweak something",
                refinement_id="ref-1",
                job_db=job_db,
                progress_callback=MagicMock(),
                tech_stack_content="",
                file_listing="",
                refinement_history=[],
                hint_file_path="src/opened_file.py",
            )

        call_kwargs = MockAgent.return_value.run.call_args.kwargs
        assert "src/opened_file.py" in call_kwargs["candidate_files"]
