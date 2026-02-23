import pytest
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
from crew_studio.refactor.runner import run_refactor_job
from crew_studio.job_database import JobDatabase

@pytest.fixture
def temp_db(tmp_path):
    db_path = tmp_path / "test_jobs.db"
    db = JobDatabase(db_path)
    return db

def _arch_md_side_effect(original_ws, plan_file, tasks):
    def _analyze(source_path):
        (original_ws / "current_architecture.md").write_text("# Arch\n")
    def _design(ts, tp=None):
        (original_ws / "target_architecture.md").write_text("# Target\n")
    def _plan(ts, tp=None):
        plan_data = {"target_stack": ts, "tasks": tasks}
        with open(plan_file, "w") as f:
            json.dump(plan_data, f)
    return _analyze, _design, _plan

@patch('crew_studio.refactor.runner.RefactorArchitectAgent')
@patch('crew_studio.refactor.runner.RefactorExecutorAgent')
def test_refactor_db_task_lifecycle(MockExecutor, MockArchitect, temp_db, tmp_path):
    """Verify that refactor tasks are correctly stored and updated in the DB."""
    job_id = "job-123"
    workspace = tmp_path / "ws"
    workspace.mkdir()
    plan_file = workspace / "refactor_plan.json"
    
    # Setup 2 tasks: one modify, one create
    tasks = [
        {"id": "t1", "file": "src/app.py", "action": "modify", "instruction": "modernize app"},
        {"id": "t2", "file": "src/new.py", "action": "create", "instruction": "create new"},
    ]
    
    # Mock Architect
    arch = MockArchitect.return_value
    analyze_se, design_se, plan_se = _arch_md_side_effect(workspace, plan_file, tasks)
    arch.analyze.side_effect = analyze_se
    arch.design.side_effect = design_se
    arch.plan.side_effect = plan_se
    
    # Mock Executor
    exec_agent = MockExecutor.return_value
    exec_agent.execute_task.return_value = "Success"
    
    # Create original file for modify
    (workspace / "src").mkdir()
    (workspace / "src" / "app.py").write_text("old code")
    
    # Run job
    run_refactor_job(
        job_id=job_id,
        workspace_path=str(workspace),
        source_path=str(workspace),
        target_stack="Modern Stack",
        job_db=temp_db
    )
    
    # VERIFY DB TASKS
    db_tasks = temp_db.get_refactor_tasks(job_id)
    assert len(db_tasks) == 2
    
    # Check first task (modify)
    t1 = next(t for t in db_tasks if t["file_path"] == "src/app.py")
    assert t1["action"] == "modify"
    assert t1["status"] == "completed"
    assert t1["instruction"] == "modernize app"
    
    # Check second task (create)
    t2 = next(t for t in db_tasks if t["file_path"] == "src/new.py")
    assert t2["action"] == "create"
    assert t2["status"] == "completed"
    
    # Check summary
    summary = temp_db.get_refactor_summary(job_id)
    assert summary["total"] == 2
    assert summary["completed"] == 2

@patch('crew_studio.refactor.runner.RefactorArchitectAgent')
@patch('crew_studio.refactor.runner.RefactorExecutorAgent')
def test_refactor_large_file_surgical_edit_logic(MockExecutor, MockArchitect, temp_db, tmp_path):
    """Verify that RefactorExecutorAgent is instructed correctly for large files."""
    from agent.src.ai_software_dev_crew.refactor.agents.executor_agent import RefactorExecutorAgent
    
    # Patch BaseLlamaIndexAgent.chat to capture the prompt
    with patch("agent.src.llamaindex_crew.agents.base_agent.BaseLlamaIndexAgent.chat") as mock_chat:
        mock_chat.return_value = "Mocked Response"
        
        agent = RefactorExecutorAgent(workspace_path=str(tmp_path), job_id="test-large")
        
        # Small file
        agent.execute_task("small.py", "fix", action="modify", source_content="small")
        assert mock_chat.called
        prompt_small = mock_chat.call_args[0][0]
        assert "file_writer" in prompt_small
        assert "file_line_replacer" not in prompt_small
        
        mock_chat.reset_mock()
        
        # Large file (> 30000 chars)
        large_content = "x" * 31000
        agent.execute_task("large.py", "fix", action="modify", source_content=large_content)
        assert mock_chat.called
        prompt_large = mock_chat.call_args[0][0]
        assert "LARGE FILE" in prompt_large
        assert "file_line_replacer" in prompt_large
        assert "file_writer" in prompt_large # It's in the negative instruction (Do NOT use)
        assert "1: xxxxx" in prompt_large # Line numbering check

@patch('crew_studio.refactor.runner.RefactorArchitectAgent')
@patch('crew_studio.refactor.runner.RefactorExecutorAgent')
def test_refactor_task_failure_updates_db(MockExecutor, MockArchitect, temp_db, tmp_path):
    """Verify that task failure correctly updates the DB status and error message."""
    job_id = "job-fail"
    workspace = tmp_path / "ws-fail"
    workspace.mkdir()
    plan_file = workspace / "refactor_plan.json"
    
    tasks = [{"id": "t1", "file": "failed.py", "action": "modify", "instruction": "fail me"}]
    
    arch = MockArchitect.return_value
    analyze_se, design_se, plan_se = _arch_md_side_effect(workspace, plan_file, tasks)
    arch.analyze.side_effect = analyze_se
    arch.design.side_effect = design_se
    arch.plan.side_effect = plan_se
    
    # Mock Executor to fail
    exec_agent = MockExecutor.return_value
    exec_agent.execute_task.side_effect = Exception("LLM connection timed out")
    
    (workspace / "failed.py").write_text("old")
    
    run_refactor_job(
        job_id=job_id,
        workspace_path=str(workspace),
        source_path=str(workspace),
        target_stack="Stack",
        job_db=temp_db
    )
    
    db_tasks = temp_db.get_refactor_tasks(job_id)
    assert len(db_tasks) == 1
    assert db_tasks[0]["status"] == "failed"
    assert "LLM connection timed out" in db_tasks[0]["error"]

@patch('crew_studio.refactor.runner.RefactorArchitectAgent')
@patch('crew_studio.refactor.runner.RefactorExecutorAgent')
def test_workspace_restructuring_and_isolation(MockExecutor, MockArchitect, temp_db, tmp_path):
    """Verify that legacy files are moved to original_source/ and kept there."""
    job_id = "job-restruct"
    workspace = tmp_path / "ws-restruct"
    workspace.mkdir()
    
    # Create some legacy files
    (workspace / "legacy.txt").write_text("old content")
    (workspace / "subdir").mkdir()
    (workspace / "subdir" / "data.py").write_text("data")
    
    # Create system file that should NOT be moved
    (workspace / "crew_jobs.db").write_text("db content")
    
    # Mock Architect/Executor
    arch = MockArchitect.return_value
    exec_agent = MockExecutor.return_value
    
    # Prepare a minimal plan
    plan_file = workspace / "refactor_plan.json"
    tasks = [{"id": "t1", "file": "legacy.txt", "action": "modify", "instruction": "fix"}]
    analyze_se, design_se, plan_se = _arch_md_side_effect(workspace, plan_file, tasks)
    arch.analyze.side_effect = analyze_se
    arch.design.side_effect = design_se
    arch.plan.side_effect = plan_se
    
    # Run job
    run_refactor_job(
        job_id=job_id,
        workspace_path=str(workspace),
        source_path=str(workspace),
        target_stack="Stack",
        job_db=temp_db
    )
    
    # VERIFY MOVEMENT
    # legacy.txt should be in original_source/legacy.txt
    assert (workspace / "original_source" / "legacy.txt").exists()
    assert (workspace / "original_source" / "subdir" / "data.py").exists()
    
    # legacy.txt should NOT be in the root anymore
    assert not (workspace / "legacy.txt").exists()
    assert not (workspace / "subdir").exists()
    
    # crew_jobs.db should still be in the root
    assert (workspace / "crew_jobs.db").exists()
    
    # refactored/ should exist
    assert (workspace / "refactored").is_dir()
    
    # Verify architect was called with the NEW source_path
    arch.analyze.assert_called_with(str(workspace / "original_source"))
