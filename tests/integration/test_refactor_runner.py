
import pytest
import json
import os
from unittest.mock import MagicMock, patch, mock_open
from crew_studio.refactor.runner import run_refactor_job, REFACTORED_OUTPUT_DIR


@pytest.fixture(autouse=True)
def disable_devops_agent():
    """Skip the DevOps agent in refactor tests so they don't call the real LLM."""
    with patch("crew_studio.refactor.runner.DEVOPS_AGENT_AVAILABLE", False):
        yield


# ---------------------------------------------------------------------------
# Helper: builds a plan JSON and writes it as a side-effect of architect.plan()
# ---------------------------------------------------------------------------
def _plan_side_effect(plan_file, tasks, target_stack="Modern Stack"):
    """Return a side_effect callable that writes a plan file."""
    def _write(ts, tp=None):
        plan_data = {"target_stack": ts, "tasks": tasks}
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        with open(plan_file, "w") as f:
            json.dump(plan_data, f)
    return _write


def _arch_md_side_effect(original_ws, plan_file, tasks, target_stack="Modern Stack"):
    """Side-effects for analyze, design, and plan.

    Architect runs on the **original** workspace; all artifacts are written there.
    The runner then copies docs into refactored/ for co-location.

    ``original_ws`` is the job workspace (Path). ``plan_file`` should be
    original_ws / "refactor_plan.json".

    Returns (analyze_se, design_se, plan_se).
    """
    def _analyze(source_path):
        original_ws.mkdir(parents=True, exist_ok=True)
        (original_ws / "current_architecture.md").write_text("# Architecture\n")
    def _design(ts, tp=None):
        original_ws.mkdir(parents=True, exist_ok=True)
        (original_ws / "target_architecture.md").write_text("# Target Architecture\n")
    def _plan(ts, tp=None):
        plan_data = {"target_stack": ts, "tasks": tasks}
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        with open(plan_file, "w") as f:
            json.dump(plan_data, f)
    return _analyze, _design, _plan


def _refactored(workspace):
    """Convenience: return path to the refactored subdirectory."""
    return workspace / REFACTORED_OUTPUT_DIR


# ===========================================================================
# Existing tests (updated to expect output under refactored/)
# ===========================================================================

@patch('crew_studio.refactor.runner.RefactorArchitectAgent')
@patch('crew_studio.refactor.runner.RefactorExecutorAgent')
def test_run_refactor_job_success(MockExecutor, MockArchitect, tmp_path):
    # Setup mocks
    architect_instance = MockArchitect.return_value
    executor_instance = MockExecutor.return_value
    
    # Setup filesystem (architect writes to original workspace)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ref = _refactored(workspace)
    plan_file = workspace / "refactor_plan.json"
    
    analyze_se, design_se, plan_se = _arch_md_side_effect(
        workspace, plan_file,
        tasks=[{"id": "1", "file": "src/main.py", "action": "modify", "instruction": "Update imports"}],
    )
    architect_instance.analyze.side_effect = analyze_se
    architect_instance.design.side_effect = design_se
    architect_instance.plan.side_effect = plan_se

    # Create file so modify task can copy it
    (workspace / "src").mkdir(parents=True, exist_ok=True)
    (workspace / "src" / "main.py").write_text("legacy")

    # Capture progress callbacks
    progress_calls = []
    def progress_callback(phase, percent, msg):
        progress_calls.append((phase, percent, msg))

    # Run the function
    result = run_refactor_job(
        job_id="test-job",
        workspace_path=str(workspace),
        source_path=str(workspace),
        target_stack="Modern Stack",
        progress_callback=progress_callback
    )

    # Architect on original workspace, executor on refactored/
    MockArchitect.assert_called_once_with(str(workspace), "test-job")
    MockExecutor.assert_called_once_with(str(ref), "test-job")

    # Verifications
    architect_instance.analyze.assert_called_once()
    architect_instance.design.assert_called_once_with("Modern Stack", "")
    architect_instance.plan.assert_called_once_with("Modern Stack", "")
    
    # Verify executor was called for the task (modify: source_content passed, no copy of old file)
    executor_instance.execute_task.assert_called_once()
    call = executor_instance.execute_task.call_args
    assert call[0][0] == "src/main.py" and call[0][1] == "Update imports"
    assert call[1]["action"] == "modify"
    assert call[1].get("source_content") == "legacy"
    
    # Verify callbacks include design phase
    phases = [p[0] for p in progress_calls]
    assert "analysis" in phases
    assert "design" in phases
    assert "planning" in phases
    assert "execution" in phases
    assert "completed" in phases

@patch('crew_studio.refactor.runner.RefactorArchitectAgent')
@patch('crew_studio.refactor.runner.RefactorExecutorAgent')
def test_run_refactor_job_plan_missing(MockExecutor, MockArchitect, tmp_path):
    # Test case where Architect fails to create plan (architect writes to original workspace)
    architect_instance = MockArchitect.return_value
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    architect_instance.analyze.side_effect = lambda sp: (workspace / "current_architecture.md").write_text("# Arch\n")
    architect_instance.design.side_effect = lambda ts, tp=None: (workspace / "target_architecture.md").write_text("# Target\n")
    # No side effect for plan() -> file won't be created

    with pytest.raises(FileNotFoundError):
        run_refactor_job(
            job_id="test-job",
            workspace_path=str(workspace),
            source_path=str(workspace),
            target_stack="Modern Stack"
        )


# ===========================================================================
# Fix 1: Runner update_progress should call job_db.update_progress directly
# ===========================================================================

@patch('crew_studio.refactor.runner.RefactorArchitectAgent')
@patch('crew_studio.refactor.runner.RefactorExecutorAgent')
def test_run_refactor_job_updates_job_db_progress(MockExecutor, MockArchitect, tmp_path):
    """job_db.update_progress(job_id, phase, pct, msg) must be called when job_db is provided."""
    architect_instance = MockArchitect.return_value
    executor_instance = MockExecutor.return_value

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    plan_file = workspace / "refactor_plan.json"

    analyze_se, design_se, plan_se = _arch_md_side_effect(
        workspace, plan_file,
        tasks=[{"id": "1", "file": "a.py", "action": "modify", "instruction": "fix"}],
    )
    architect_instance.analyze.side_effect = analyze_se
    architect_instance.design.side_effect = design_se
    architect_instance.plan.side_effect = plan_se

    (workspace / "a.py").write_text("# fix")
    job_db = MagicMock()

    run_refactor_job(
        job_id="j1",
        workspace_path=str(workspace),
        source_path=str(workspace),
        target_stack="Stack",
        job_db=job_db,
    )

    # job_db.update_progress must have been called at least once per phase
    calls = job_db.update_progress.call_args_list
    phases_called = [c[0][1] for c in calls]  # positional arg index 1 = phase
    assert "analysis" in phases_called
    assert "planning" in phases_called
    assert "execution" in phases_called
    assert "completed" in phases_called

    # Every call should start with job_id
    for c in calls:
        assert c[0][0] == "j1"


# ===========================================================================
# Fix 2: Runner branches on action (modify / delete / create)
# ===========================================================================

@patch('crew_studio.refactor.runner.RefactorArchitectAgent')
@patch('crew_studio.refactor.runner.RefactorExecutorAgent')
def test_run_refactor_job_delete_action(MockExecutor, MockArchitect, tmp_path):
    """action=delete is skipped in greenfield (nothing in refactored/ to delete); original unchanged."""
    architect_instance = MockArchitect.return_value
    executor_instance = MockExecutor.return_value

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    original_file = workspace / "old_service.py"
    original_file.write_text("# legacy code")

    plan_file = workspace / "refactor_plan.json"
    analyze_se, design_se, plan_se = _arch_md_side_effect(
        workspace, plan_file,
        tasks=[{"id": "1", "file": "old_service.py", "action": "delete", "instruction": "Remove legacy service"}],
    )
    architect_instance.analyze.side_effect = analyze_se
    architect_instance.design.side_effect = design_se
    architect_instance.plan.side_effect = plan_se

    run_refactor_job(
        job_id="j-del",
        workspace_path=str(workspace),
        source_path=str(workspace),
        target_stack="Stack",
    )

    # Greenfield: delete is skipped; refactored/ never had old_service.py
    ref = _refactored(workspace)
    assert not (ref / "old_service.py").exists()
    # Original unchanged
    assert original_file.exists(), "original file must be preserved in workspace root"
    executor_instance.execute_task.assert_not_called()


@patch('crew_studio.refactor.runner.RefactorArchitectAgent')
@patch('crew_studio.refactor.runner.RefactorExecutorAgent')
def test_run_refactor_job_create_action(MockExecutor, MockArchitect, tmp_path):
    """action=create should call executor.execute_task with create instruction."""
    architect_instance = MockArchitect.return_value
    executor_instance = MockExecutor.return_value

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    plan_file = workspace / "refactor_plan.json"
    analyze_se, design_se, plan_se = _arch_md_side_effect(
        workspace, plan_file,
        tasks=[{"id": "1", "file": "new_service.py", "action": "create", "instruction": "Create REST controller"}],
    )
    architect_instance.analyze.side_effect = analyze_se
    architect_instance.design.side_effect = design_se
    architect_instance.plan.side_effect = plan_se

    run_refactor_job(
        job_id="j-create",
        workspace_path=str(workspace),
        source_path=str(workspace),
        target_stack="Stack",
    )

    executor_instance.execute_task.assert_called_once_with("new_service.py", "Create REST controller", action="create")


@patch('crew_studio.refactor.runner.RefactorArchitectAgent')
@patch('crew_studio.refactor.runner.RefactorExecutorAgent')
def test_run_refactor_job_mixed_actions(MockExecutor, MockArchitect, tmp_path):
    """Plan with modify + delete + create should handle each correctly."""
    architect_instance = MockArchitect.return_value
    executor_instance = MockExecutor.return_value

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "app.py").write_text("# app")
    (workspace / "legacy.py").write_text("# old")

    plan_file = workspace / "refactor_plan.json"
    tasks = [
        {"id": "1", "file": "app.py", "action": "modify", "instruction": "Update imports"},
        {"id": "2", "file": "legacy.py", "action": "delete", "instruction": "Remove legacy module"},
        {"id": "3", "file": "new_api.py", "action": "create", "instruction": "Create new API"},
    ]
    analyze_se, design_se, plan_se = _arch_md_side_effect(workspace, plan_file, tasks=tasks)
    architect_instance.analyze.side_effect = analyze_se
    architect_instance.design.side_effect = design_se
    architect_instance.plan.side_effect = plan_se

    run_refactor_job(
        job_id="j-mixed",
        workspace_path=str(workspace),
        source_path=str(workspace),
        target_stack="Stack",
    )

    ref = _refactored(workspace)
    # Delete skipped in greenfield; legacy.py never copied to refactored/
    assert not (ref / "legacy.py").exists()
    assert (workspace / "legacy.py").exists(), "original must be preserved"

    # Modify + create: executor called twice (not for delete)
    assert executor_instance.execute_task.call_count == 2
    calls = executor_instance.execute_task.call_args_list
    assert calls[0][0] == ("app.py", "Update imports") and calls[0][1].get("action") == "modify"
    assert calls[1][0] == ("new_api.py", "Create new API") and calls[1][1].get("action") == "create"


# ===========================================================================
# Fix 3: Runner checks for current_architecture.md after analyze()
# ===========================================================================

@patch('crew_studio.refactor.runner.RefactorArchitectAgent')
@patch('crew_studio.refactor.runner.RefactorExecutorAgent')
def test_run_refactor_job_warns_missing_architecture_md(MockExecutor, MockArchitect, tmp_path, caplog):
    """Runner should log a warning if current_architecture.md is missing after analyze."""
    architect_instance = MockArchitect.return_value
    executor_instance = MockExecutor.return_value

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ref = _refactored(workspace)
    plan_file = workspace / "refactor_plan.json"

    # analyze does NOT create current_architecture.md
    architect_instance.analyze.return_value = "done"
    architect_instance.design.side_effect = lambda ts, tp=None: (workspace / "target_architecture.md").write_text("# Target\n")
    architect_instance.plan.side_effect = _plan_side_effect(
        plan_file,
        tasks=[{"id": "1", "file": "a.py", "action": "modify", "instruction": "fix"}],
    )

    import logging
    with caplog.at_level(logging.WARNING, logger="crew_studio.refactor.runner"):
        run_refactor_job(
            job_id="j-noarch",
            workspace_path=str(workspace),
            source_path=str(workspace),
            target_stack="Stack",
        )

    assert any("current_architecture.md" in msg for msg in caplog.messages)


@patch('crew_studio.refactor.runner.RefactorArchitectAgent')
@patch('crew_studio.refactor.runner.RefactorExecutorAgent')
def test_run_refactor_job_no_warn_when_architecture_md_exists(MockExecutor, MockArchitect, tmp_path, caplog):
    """No warning when current_architecture.md exists."""
    architect_instance = MockArchitect.return_value
    executor_instance = MockExecutor.return_value

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    plan_file = workspace / "refactor_plan.json"
    (workspace / "a.py").write_text("# a")

    analyze_se, design_se, plan_se = _arch_md_side_effect(
        workspace, plan_file,
        tasks=[{"id": "1", "file": "a.py", "action": "modify", "instruction": "fix"}],
    )
    architect_instance.analyze.side_effect = analyze_se
    architect_instance.design.side_effect = design_se
    architect_instance.plan.side_effect = plan_se

    import logging
    with caplog.at_level(logging.WARNING, logger="crew_studio.refactor.runner"):
        run_refactor_job(
            job_id="j-arch-ok",
            workspace_path=str(workspace),
            source_path=str(workspace),
            target_stack="Stack",
        )

    assert not any("current_architecture.md" in msg for msg in caplog.messages)


# ===========================================================================
# Fix 4: Plan validation — required keys and non-empty fields
# ===========================================================================

@patch('crew_studio.refactor.runner.RefactorArchitectAgent')
@patch('crew_studio.refactor.runner.RefactorExecutorAgent')
def test_run_refactor_job_plan_task_missing_file(MockExecutor, MockArchitect, tmp_path):
    """A task without 'file' should raise ValueError."""
    architect_instance = MockArchitect.return_value

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ref = _refactored(workspace)
    plan_file = workspace / "refactor_plan.json"

    analyze_se, design_se, plan_se = _arch_md_side_effect(
        workspace, plan_file,
        tasks=[{"id": "1", "action": "modify", "instruction": "fix"}],  # no "file"
    )
    architect_instance.analyze.side_effect = analyze_se
    architect_instance.design.side_effect = design_se
    architect_instance.plan.side_effect = plan_se

    with pytest.raises(ValueError, match="file"):
        run_refactor_job(
            job_id="j-val1",
            workspace_path=str(workspace),
            source_path=str(workspace),
            target_stack="Stack",
        )


@patch('crew_studio.refactor.runner.RefactorArchitectAgent')
@patch('crew_studio.refactor.runner.RefactorExecutorAgent')
def test_run_refactor_job_plan_task_empty_file(MockExecutor, MockArchitect, tmp_path):
    """A task with empty 'file' should raise ValueError."""
    architect_instance = MockArchitect.return_value

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ref = _refactored(workspace)
    plan_file = workspace / "refactor_plan.json"

    analyze_se, design_se, plan_se = _arch_md_side_effect(
        workspace, plan_file,
        tasks=[{"id": "1", "file": "", "action": "modify", "instruction": "fix"}],
    )
    architect_instance.analyze.side_effect = analyze_se
    architect_instance.design.side_effect = design_se
    architect_instance.plan.side_effect = plan_se

    with pytest.raises(ValueError, match="file"):
        run_refactor_job(
            job_id="j-val2",
            workspace_path=str(workspace),
            source_path=str(workspace),
            target_stack="Stack",
        )


@patch('crew_studio.refactor.runner.RefactorArchitectAgent')
@patch('crew_studio.refactor.runner.RefactorExecutorAgent')
def test_run_refactor_job_plan_task_missing_instruction_modify(MockExecutor, MockArchitect, tmp_path):
    """A modify task without 'instruction' should raise ValueError."""
    architect_instance = MockArchitect.return_value

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ref = _refactored(workspace)
    plan_file = workspace / "refactor_plan.json"

    analyze_se, design_se, plan_se = _arch_md_side_effect(
        workspace, plan_file,
        tasks=[{"id": "1", "file": "a.py", "action": "modify"}],  # no instruction
    )
    architect_instance.analyze.side_effect = analyze_se
    architect_instance.design.side_effect = design_se
    architect_instance.plan.side_effect = plan_se

    with pytest.raises(ValueError, match="instruction"):
        run_refactor_job(
            job_id="j-val3",
            workspace_path=str(workspace),
            source_path=str(workspace),
            target_stack="Stack",
        )


@patch('crew_studio.refactor.runner.RefactorArchitectAgent')
@patch('crew_studio.refactor.runner.RefactorExecutorAgent')
def test_run_refactor_job_plan_task_missing_instruction_delete_ok(MockExecutor, MockArchitect, tmp_path):
    """A delete task without 'instruction' is acceptable — instruction is optional for deletes."""
    architect_instance = MockArchitect.return_value

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "old.py").write_text("# old")

    ref = _refactored(workspace)
    plan_file = workspace / "refactor_plan.json"
    analyze_se, design_se, plan_se = _arch_md_side_effect(
        workspace, plan_file,
        tasks=[{"id": "1", "file": "old.py", "action": "delete"}],  # no instruction
    )
    architect_instance.analyze.side_effect = analyze_se
    architect_instance.design.side_effect = design_se
    architect_instance.plan.side_effect = plan_se

    # Should NOT raise
    run_refactor_job(
        job_id="j-val4",
        workspace_path=str(workspace),
        source_path=str(workspace),
        target_stack="Stack",
    )

    assert not (ref / "old.py").exists()
    assert (workspace / "old.py").exists(), "original preserved"


# ===========================================================================
# Fix 5: Architect prompt instructs workspace-relative paths
# (tested via unit test on the prompt content — see test_refactor_architect.py)
# ===========================================================================


# ===========================================================================
# Fix 6: Executor failure records which task failed and continues
# ===========================================================================

@patch('crew_studio.refactor.runner.RefactorArchitectAgent')
@patch('crew_studio.refactor.runner.RefactorExecutorAgent')
def test_run_refactor_job_executor_failure_continues(MockExecutor, MockArchitect, tmp_path):
    """If executor fails on one task, runner continues with the rest and returns failures."""
    architect_instance = MockArchitect.return_value
    executor_instance = MockExecutor.return_value

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ref = _refactored(workspace)
    plan_file = workspace / "refactor_plan.json"

    (workspace / "a.py").write_text("# a")
    (workspace / "b.py").write_text("# b")
    (workspace / "c.py").write_text("# c")
    tasks = [
        {"id": "1", "file": "a.py", "action": "modify", "instruction": "fix a"},
        {"id": "2", "file": "b.py", "action": "modify", "instruction": "fix b"},
        {"id": "3", "file": "c.py", "action": "modify", "instruction": "fix c"},
    ]
    analyze_se, design_se, plan_se = _arch_md_side_effect(workspace, plan_file, tasks=tasks)
    architect_instance.analyze.side_effect = analyze_se
    architect_instance.design.side_effect = design_se
    architect_instance.plan.side_effect = plan_se

    # Executor: task 2 fails, tasks 1 and 3 succeed
    def execute_side_effect(file_path, instruction, action="modify", source_content=None):
        if file_path == "b.py":
            raise RuntimeError("LLM error on b.py")
        return "ok"
    executor_instance.execute_task.side_effect = execute_side_effect

    result = run_refactor_job(
        job_id="j-fail",
        workspace_path=str(workspace),
        source_path=str(workspace),
        target_stack="Stack",
    )

    # All three tasks should have been attempted
    assert executor_instance.execute_task.call_count == 3

    # Result should report failures
    assert result["total_tasks"] == 3
    assert result["completed_tasks"] == 2
    assert len(result["failed_tasks"]) == 1
    assert result["failed_tasks"][0]["task_id"] == "2"
    assert result["failed_tasks"][0]["file"] == "b.py"
    assert "LLM error" in result["failed_tasks"][0]["error"]


@patch('crew_studio.refactor.runner.RefactorArchitectAgent')
@patch('crew_studio.refactor.runner.RefactorExecutorAgent')
def test_run_refactor_job_returns_result_on_success(MockExecutor, MockArchitect, tmp_path):
    """On full success, result should show all tasks completed with no failures."""
    architect_instance = MockArchitect.return_value
    executor_instance = MockExecutor.return_value

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ref = _refactored(workspace)
    plan_file = workspace / "refactor_plan.json"

    (workspace / "a.py").write_text("# a")
    (workspace / "b.py").write_text("# b")
    analyze_se, design_se, plan_se = _arch_md_side_effect(
        workspace, plan_file,
        tasks=[
            {"id": "1", "file": "a.py", "action": "modify", "instruction": "fix a"},
            {"id": "2", "file": "b.py", "action": "modify", "instruction": "fix b"},
        ],
    )
    architect_instance.analyze.side_effect = analyze_se
    architect_instance.design.side_effect = design_se
    architect_instance.plan.side_effect = plan_se

    result = run_refactor_job(
        job_id="j-ok",
        workspace_path=str(workspace),
        source_path=str(workspace),
        target_stack="Stack",
    )

    assert result["total_tasks"] == 2
    assert result["completed_tasks"] == 2
    assert result["failed_tasks"] == []


@patch("crew_studio.refactor.runner.RefactorArchitectAgent")
@patch("crew_studio.refactor.runner.RefactorExecutorAgent")
def test_run_refactor_job_calls_devops_phase_when_available(MockExecutor, MockArchitect, tmp_path):
    """When DEVOPS_AGENT_AVAILABLE is True, DevOpsAgent.run is called after execution."""
    architect_instance = MockArchitect.return_value
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ref = _refactored(workspace)
    plan_file = workspace / "refactor_plan.json"
    analyze_se, design_se, plan_se = _arch_md_side_effect(
        workspace, plan_file,
        tasks=[{"id": "1", "file": "a.py", "action": "modify", "instruction": "fix"}],
    )
    architect_instance.analyze.side_effect = analyze_se
    architect_instance.design.side_effect = design_se
    architect_instance.plan.side_effect = plan_se

    (workspace / "a.py").write_text("# a")
    with patch("crew_studio.refactor.runner.DEVOPS_AGENT_AVAILABLE", True), \
         patch("crew_studio.refactor.runner.DevOpsAgent") as MockDevOpsAgent:
        devops_instance = MockDevOpsAgent.return_value
        result = run_refactor_job(
            job_id="j-devops",
            workspace_path=str(workspace),
            source_path=str(workspace),
            target_stack="Python 3.11, FastAPI",
            devops_instructions="Use UBI9 only.",
        )

    ref = _refactored(workspace)
    # DevOps agent must receive the refactored dir, not the workspace root
    MockDevOpsAgent.assert_called_once_with(ref, "j-devops")
    devops_instance.run.assert_called_once()
    run_kw = devops_instance.run.call_args[1]
    assert run_kw.get("tech_stack") == "Python 3.11, FastAPI"
    assert run_kw.get("pipeline_type") == "tekton"
    assert run_kw.get("project_context") == "Use UBI9 only."
    assert result["failed_tasks"] == []


# ===========================================================================
# NEW: Refactored-subdirectory contract tests
# ===========================================================================

@patch("crew_studio.refactor.runner.RefactorArchitectAgent")
@patch("crew_studio.refactor.runner.RefactorExecutorAgent")
def test_refactored_subdir_exists_after_run(MockExecutor, MockArchitect, tmp_path):
    """After run_refactor_job the refactored/ subdir must exist."""
    architect_instance = MockArchitect.return_value
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.py").write_text("# a")
    plan_file = workspace / "refactor_plan.json"
    analyze_se, design_se, plan_se = _arch_md_side_effect(
        workspace, plan_file,
        tasks=[{"id": "1", "file": "a.py", "action": "modify", "instruction": "fix"}],
    )
    architect_instance.analyze.side_effect = analyze_se
    architect_instance.design.side_effect = design_se
    architect_instance.plan.side_effect = plan_se

    run_refactor_job(
        job_id="j-subdir",
        workspace_path=str(workspace),
        source_path=str(workspace),
        target_stack="Stack",
    )

    ref = _refactored(workspace)
    assert ref.is_dir(), "refactored/ subdir must exist after a run"


@patch("crew_studio.refactor.runner.RefactorArchitectAgent")
@patch("crew_studio.refactor.runner.RefactorExecutorAgent")
def test_original_files_preserved_after_refactor(MockExecutor, MockArchitect, tmp_path):
    """Original workspace files must be unchanged after a refactor run."""
    architect_instance = MockArchitect.return_value
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    # Create original files
    (workspace / "src").mkdir()
    (workspace / "src" / "main.py").write_text("original_content")
    (workspace / "README.md").write_text("# Readme")

    ref = _refactored(workspace)
    plan_file = workspace / "refactor_plan.json"
    analyze_se, design_se, plan_se = _arch_md_side_effect(
        workspace, plan_file,
        tasks=[{"id": "1", "file": "src/main.py", "action": "modify", "instruction": "modernize"}],
    )
    architect_instance.analyze.side_effect = analyze_se
    architect_instance.design.side_effect = design_se
    architect_instance.plan.side_effect = plan_se

    run_refactor_job(
        job_id="j-preserve",
        workspace_path=str(workspace),
        source_path=str(workspace),
        target_stack="Stack",
    )

    # Original content unchanged
    assert (workspace / "src" / "main.py").read_text() == "original_content"
    assert (workspace / "README.md").read_text() == "# Readme"

    # Modify task: runner passes source_content to executor (no copy of old code into refactored/)
    MockExecutor.return_value.execute_task.assert_called_once()
    call = MockExecutor.return_value.execute_task.call_args
    assert call[1].get("source_content") == "original_content"
    assert call[1].get("action") == "modify"


@patch("crew_studio.refactor.runner.RefactorArchitectAgent")
@patch("crew_studio.refactor.runner.RefactorExecutorAgent")
def test_plan_written_under_refactored_not_root(MockExecutor, MockArchitect, tmp_path):
    """Runner copies refactor_plan.json into refactored/."""
    architect_instance = MockArchitect.return_value
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.py").write_text("# a")
    plan_file = workspace / "refactor_plan.json"
    analyze_se, design_se, plan_se = _arch_md_side_effect(
        workspace, plan_file,
        tasks=[{"id": "1", "file": "a.py", "action": "modify", "instruction": "fix"}],
    )
    architect_instance.analyze.side_effect = analyze_se
    architect_instance.design.side_effect = design_se
    architect_instance.plan.side_effect = plan_se

    run_refactor_job(
        job_id="j-plan-loc",
        workspace_path=str(workspace),
        source_path=str(workspace),
        target_stack="Stack",
    )

    ref = _refactored(workspace)
    assert (ref / "refactor_plan.json").exists(), "runner copies plan into refactored/"


@patch("crew_studio.refactor.runner.RefactorArchitectAgent")
@patch("crew_studio.refactor.runner.RefactorExecutorAgent")
def test_rerun_replaces_refactored_dir(MockExecutor, MockArchitect, tmp_path):
    """Running refactor twice replaces refactored/ with a fresh copy."""
    architect_instance = MockArchitect.return_value
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "app.py").write_text("v1")

    ref = _refactored(workspace)

    def setup_mocks():
        plan_file = workspace / "refactor_plan.json"
        analyze_se, design_se, plan_se = _arch_md_side_effect(
            workspace, plan_file,
            tasks=[{"id": "1", "file": "app.py", "action": "modify", "instruction": "fix"}],
        )
        architect_instance.analyze.side_effect = analyze_se
        architect_instance.design.side_effect = design_se
        architect_instance.plan.side_effect = plan_se

    # First run
    setup_mocks()
    run_refactor_job(
        job_id="j-rerun",
        workspace_path=str(workspace),
        source_path=str(workspace),
        target_stack="Stack",
    )

    # Simulate that the first run modified a file in refactored/
    (ref / "leftover.txt").write_text("from run 1")

    # Second run — should start fresh
    setup_mocks()
    run_refactor_job(
        job_id="j-rerun",
        workspace_path=str(workspace),
        source_path=str(workspace),
        target_stack="Stack",
    )

    assert not (ref / "leftover.txt").exists(), "stale artifacts from previous run must be removed"
    # Original still intact
    assert (workspace / "app.py").read_text() == "v1"


# ===========================================================================
# NEW: Design phase (target_architecture.md) tests
# ===========================================================================

@patch("crew_studio.refactor.runner.RefactorArchitectAgent")
@patch("crew_studio.refactor.runner.RefactorExecutorAgent")
def test_target_architecture_written_under_refactored(MockExecutor, MockArchitect, tmp_path):
    """target_architecture.md must be created under refactored/ by the design phase."""
    architect_instance = MockArchitect.return_value
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ref = _refactored(workspace)
    plan_file = workspace / "refactor_plan.json"
    analyze_se, design_se, plan_se = _arch_md_side_effect(
        workspace, plan_file,
        tasks=[{"id": "1", "file": "a.py", "action": "modify", "instruction": "fix"}],
    )
    architect_instance.analyze.side_effect = analyze_se
    architect_instance.design.side_effect = design_se
    architect_instance.plan.side_effect = plan_se
    (workspace / "a.py").write_text("# a")

    run_refactor_job(
        job_id="j-target-arch",
        workspace_path=str(workspace),
        source_path=str(workspace),
        target_stack="Stack",
    )

    ref = _refactored(workspace)
    assert (ref / "target_architecture.md").exists(), "runner copies target_architecture.md into refactored/"


@patch("crew_studio.refactor.runner.RefactorArchitectAgent")
@patch("crew_studio.refactor.runner.RefactorExecutorAgent")
def test_design_called_before_plan(MockExecutor, MockArchitect, tmp_path):
    """design() must be called after analyze() and before plan()."""
    architect_instance = MockArchitect.return_value
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    plan_file = workspace / "refactor_plan.json"

    call_order = []

    def _analyze(sp):
        call_order.append("analyze")
        (workspace / "current_architecture.md").write_text("# Arch\n")

    def _design(ts, tp=None):
        call_order.append("design")
        (workspace / "target_architecture.md").write_text("# Target\n")

    def _plan(ts, tp=None):
        call_order.append("plan")
        plan_data = {"target_stack": ts, "tasks": [
            {"id": "1", "file": "a.py", "action": "modify", "instruction": "fix"}
        ]}
        with open(plan_file, "w") as f:
            json.dump(plan_data, f)

    architect_instance.analyze.side_effect = _analyze
    architect_instance.design.side_effect = _design
    architect_instance.plan.side_effect = _plan
    (workspace / "a.py").write_text("# a")

    run_refactor_job(
        job_id="j-order",
        workspace_path=str(workspace),
        source_path=str(workspace),
        target_stack="Stack",
    )

    assert call_order == ["analyze", "design", "plan"], (
        f"Expected analyze -> design -> plan, got {call_order}"
    )


@patch("crew_studio.refactor.runner.RefactorArchitectAgent")
@patch("crew_studio.refactor.runner.RefactorExecutorAgent")
def test_runner_warns_missing_target_architecture_md(MockExecutor, MockArchitect, tmp_path, caplog):
    """Runner should log a warning if target_architecture.md is missing after design."""
    architect_instance = MockArchitect.return_value
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    plan_file = workspace / "refactor_plan.json"
    (workspace / "a.py").write_text("# a")

    analyze_se, _, plan_se = _arch_md_side_effect(
        workspace, plan_file,
        tasks=[{"id": "1", "file": "a.py", "action": "modify", "instruction": "fix"}],
    )
    architect_instance.analyze.side_effect = analyze_se
    # design does NOT create target_architecture.md
    architect_instance.design.return_value = "done"
    architect_instance.plan.side_effect = plan_se

    import logging
    with caplog.at_level(logging.WARNING, logger="crew_studio.refactor.runner"):
        run_refactor_job(
            job_id="j-no-target",
            workspace_path=str(workspace),
            source_path=str(workspace),
            target_stack="Stack",
        )

    assert any("target_architecture.md" in msg for msg in caplog.messages)


# ===========================================================================
# Greenfield refactored/ — TDD tests (refactored/ starts empty, copy-on-need)
# ===========================================================================

@patch("crew_studio.refactor.runner.RefactorArchitectAgent")
@patch("crew_studio.refactor.runner.RefactorExecutorAgent")
def test_refactored_dir_starts_empty(MockExecutor, MockArchitect, tmp_path):
    """refactored/ has no legacy source files; only create tasks -> no src/main.py in refactored/."""
    architect_instance = MockArchitect.return_value
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "src").mkdir()
    (workspace / "src" / "main.py").write_text("legacy code")
    plan_file = workspace / "refactor_plan.json"

    def _analyze(sp):
        (workspace / "current_architecture.md").write_text("# Arch\n")
    def _design(ts, tp=None):
        (workspace / "target_architecture.md").write_text("# Target\n")
    def _plan(ts, tp=None):
        with open(plan_file, "w") as f:
            json.dump({"target_stack": ts, "tasks": [
                {"id": "1", "file": "new_service.py", "action": "create", "instruction": "Create new service"}
            ]}, f)
    architect_instance.analyze.side_effect = _analyze
    architect_instance.design.side_effect = _design
    architect_instance.plan.side_effect = _plan

    run_refactor_job(
        job_id="j-greenfield",
        workspace_path=str(workspace),
        source_path=str(workspace),
        target_stack="Stack",
    )

    ref = _refactored(workspace)
    assert not (ref / "src" / "main.py").exists(), "legacy src/main.py must NOT be in refactored/ (greenfield)"


@patch("crew_studio.refactor.runner.RefactorArchitectAgent")
@patch("crew_studio.refactor.runner.RefactorExecutorAgent")
def test_modify_task_passes_source_content_no_copy(MockExecutor, MockArchitect, tmp_path):
    """For modify task, runner passes original content to executor; no copy of old code into refactored/."""
    executor_instance = MockExecutor.return_value
    architect_instance = MockArchitect.return_value
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "src").mkdir()
    (workspace / "src" / "old.py").write_text("original_content")
    plan_file = workspace / "refactor_plan.json"

    def _analyze(sp):
        (workspace / "current_architecture.md").write_text("# Arch\n")
    def _design(ts, tp=None):
        (workspace / "target_architecture.md").write_text("# Target\n")
    def _plan(ts, tp=None):
        with open(plan_file, "w") as f:
            json.dump({"target_stack": ts, "tasks": [
                {"id": "1", "file": "src/old.py", "action": "modify", "instruction": "Update to modern API"}
            ]}, f)
    architect_instance.analyze.side_effect = _analyze
    architect_instance.design.side_effect = _design
    architect_instance.plan.side_effect = _plan

    run_refactor_job(
        job_id="j-modify-copy",
        workspace_path=str(workspace),
        source_path=str(workspace),
        target_stack="Stack",
    )

    ref = _refactored(workspace)
    # Runner does NOT copy old file into refactored/; executor gets source_content and writes refactored version
    assert not (ref / "src").exists() or not (ref / "src" / "old.py").exists(), (
        "refactored/ must not contain a copy of the old file from the runner"
    )
    executor_instance.execute_task.assert_called_once()
    call = executor_instance.execute_task.call_args
    assert call[0][0] == "src/old.py"
    assert call[1].get("source_content") == "original_content"
    assert call[1].get("action") == "modify"
    assert (workspace / "src" / "old.py").read_text() == "original_content", "original unchanged"


@patch("crew_studio.refactor.runner.RefactorArchitectAgent")
@patch("crew_studio.refactor.runner.RefactorExecutorAgent")
def test_create_task_does_not_copy(MockExecutor, MockArchitect, tmp_path):
    """Create task only: workspace/app.py must NOT appear in refactored/ (no blanket copy)."""
    architect_instance = MockArchitect.return_value
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "app.py").write_text("legacy app")
    plan_file = workspace / "refactor_plan.json"

    def _analyze(sp):
        (workspace / "current_architecture.md").write_text("# Arch\n")
    def _design(ts, tp=None):
        (workspace / "target_architecture.md").write_text("# Target\n")
    def _plan(ts, tp=None):
        with open(plan_file, "w") as f:
            json.dump({"target_stack": ts, "tasks": [
                {"id": "1", "file": "new_service.py", "action": "create", "instruction": "Create service"}
            ]}, f)
    architect_instance.analyze.side_effect = _analyze
    architect_instance.design.side_effect = _design
    architect_instance.plan.side_effect = _plan

    run_refactor_job(
        job_id="j-create-only",
        workspace_path=str(workspace),
        source_path=str(workspace),
        target_stack="Stack",
    )

    ref = _refactored(workspace)
    assert not (ref / "app.py").exists(), "legacy app.py must NOT be copied (greenfield)"


@patch("crew_studio.refactor.runner.RefactorArchitectAgent")
@patch("crew_studio.refactor.runner.RefactorExecutorAgent")
def test_delete_task_skipped_in_greenfield(MockExecutor, MockArchitect, tmp_path, caplog):
    """Delete task in plan: runner skips it (log), no error."""
    architect_instance = MockArchitect.return_value
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    plan_file = workspace / "refactor_plan.json"

    def _analyze(sp):
        (workspace / "current_architecture.md").write_text("# Arch\n")
    def _design(ts, tp=None):
        (workspace / "target_architecture.md").write_text("# Target\n")
    def _plan(ts, tp=None):
        with open(plan_file, "w") as f:
            json.dump({"target_stack": ts, "tasks": [
                {"id": "1", "file": "old.py", "action": "delete", "instruction": "Remove legacy"}
            ]}, f)
    architect_instance.analyze.side_effect = _analyze
    architect_instance.design.side_effect = _design
    architect_instance.plan.side_effect = _plan

    import logging
    with caplog.at_level(logging.INFO, logger="crew_studio.refactor.runner"):
        run_refactor_job(
            job_id="j-delete-skip",
            workspace_path=str(workspace),
            source_path=str(workspace),
            target_stack="Stack",
        )

    executor_instance = MockExecutor.return_value
    executor_instance.execute_task.assert_not_called()
    assert any("delete" in msg.lower() or "skip" in msg.lower() for msg in caplog.messages)


@patch("crew_studio.refactor.runner.RefactorArchitectAgent")
@patch("crew_studio.refactor.runner.RefactorExecutorAgent")
def test_architect_reads_original_workspace(MockExecutor, MockArchitect, tmp_path):
    """Architect is initialized with original workspace_path; analyze() called with original source_path."""
    architect_instance = MockArchitect.return_value
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    plan_file = workspace / "refactor_plan.json"

    def _analyze(sp):
        (workspace / "current_architecture.md").write_text("# Arch\n")
    def _design(ts, tp=None):
        (workspace / "target_architecture.md").write_text("# Target\n")
    def _plan(ts, tp=None):
        with open(plan_file, "w") as f:
            json.dump({"target_stack": ts, "tasks": [
                {"id": "1", "file": "a.py", "action": "create", "instruction": "Create"}
            ]}, f)
    architect_instance.analyze.side_effect = _analyze
    architect_instance.design.side_effect = _design
    architect_instance.plan.side_effect = _plan

    run_refactor_job(
        job_id="j-arch-original",
        workspace_path=str(workspace),
        source_path=str(workspace),
        target_stack="Stack",
    )

    MockArchitect.assert_called_once_with(str(workspace), "j-arch-original")
    architect_instance.analyze.assert_called_once_with(str(workspace))


@patch("crew_studio.refactor.runner.RefactorArchitectAgent")
@patch("crew_studio.refactor.runner.RefactorExecutorAgent")
def test_executor_writes_to_refactored(MockExecutor, MockArchitect, tmp_path):
    """Executor is initialized with refactored dir path."""
    architect_instance = MockArchitect.return_value
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    plan_file = workspace / "refactor_plan.json"

    def _analyze(sp):
        (workspace / "current_architecture.md").write_text("# Arch\n")
    def _design(ts, tp=None):
        (workspace / "target_architecture.md").write_text("# Target\n")
    def _plan(ts, tp=None):
        with open(plan_file, "w") as f:
            json.dump({"target_stack": ts, "tasks": [
                {"id": "1", "file": "b.py", "action": "create", "instruction": "Create"}
            ]}, f)
    architect_instance.analyze.side_effect = _analyze
    architect_instance.design.side_effect = _design
    architect_instance.plan.side_effect = _plan

    run_refactor_job(
        job_id="j-exec-refactored",
        workspace_path=str(workspace),
        source_path=str(workspace),
        target_stack="Stack",
    )

    ref = _refactored(workspace)
    MockExecutor.assert_called_once_with(str(ref), "j-exec-refactored")
