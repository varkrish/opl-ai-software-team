"""
E2E test case for validation remediation using real LLM.
Runs the Tech Architect and Dev agents to resolve a seeded coding issue (syntax error)
and validates that the issue is fixed using replace_file_content.
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import pytest

_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_root))
sys.path.insert(0, str(_root / "agent"))
sys.path.insert(0, str(_root / "agent" / "src"))

from llamaindex_crew.config import ConfigLoader
from llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow
from crew_studio.job_database import JobDatabase
from llamaindex_crew.agents.tech_architect_agent import TechArchitectAgent
from llamaindex_crew.agents.dev_agent import DevAgent

@pytest.mark.e2e
@pytest.mark.slow
@pytest.mark.timeout(300)
def test_validation_remediation_e2e(tmp_path, monkeypatch):
    """Seed a coding issue and verify real LLM remediation fixes it."""
    # Isolated DB and Workspace
    db_path = tmp_path / "e2e_remediation.db"
    job_db = JobDatabase(db_path)
    
    job_id = str(uuid.uuid4())
    workspace = tmp_path / "workspace" / f"job-{job_id}"
    workspace.mkdir(parents=True)
    
    # 1. Seed a broken source file (syntax error: missing colon)
    app_file = workspace / "app.py"
    app_file.write_text("def run()\n    print('hello')\n", encoding="utf-8")
    
    # Create job in database
    job_db.create_job(job_id, "broken CLI", str(workspace))
    
    # Load config and initialize workflow
    config = ConfigLoader.load()
    wf = SoftwareDevWorkflow(
        project_id=job_id,
        workspace_path=workspace,
        vision="broken CLI",
        config=config,
        job_db=job_db,
    )
    
    # Initialize real agents using default/simple backstories
    wf.tech_stack = "Python CLI"
    wf.tech_architect_agent = TechArchitectAgent(
        custom_backstory="You are a principal software architect who provides single-paragraph fix strategies.",
        workspace_path=workspace,
    )
    wf.dev_agent = DevAgent(
        custom_backstory="You are an expert developer who modifies code files using replace_file_content.",
        workspace_path=workspace,
        config=config,
    )
    
    # 2. Call validator to identify the issue
    issues = wf._call_validator()
    assert len(issues) >= 1
    assert any(i["check"] == "integration" for i in issues)
    
    # Register the validation issues in DB
    for vi in issues:
        job_db.create_validation_issue(
            issue_id=str(uuid.uuid4()),
            job_id=job_id,
            check_name=vi["check"],
            severity=vi.get("severity", "error"),
            file_path=vi["file"],
            line_number=vi.get("line"),
            description=vi.get("description", ""),
        )
        
    # 3. Trigger remediation loop via real LLM calls
    auto_fixed = wf._auto_fix_issues(issues)
    auto_fixed_files = {i.get("file") for i in auto_fixed}
    
    attempted_fixes = []
    for vi in issues:
        if vi.get("file") in auto_fixed_files:
            continue
        if vi.get("severity") != "error":
            continue
            
        pending = job_db.get_pending_validation_issues(job_id)
        matching = [p for p in pending if p["file_path"] == vi.get("file") and p["check_name"] == vi.get("check")]
        if not matching:
            continue
        db_issue = matching[0]
        
        job_db.update_validation_issue_status(db_issue["id"], "running")
        
        # Call real Tech Architect to define strategy
        strategy = wf._get_fix_strategy(vi)
        assert strategy, "Failed to produce strategy from Tech Architect"
        
        job_db.update_validation_issue_status(db_issue["id"], "running", fix_strategy=strategy)
        
        # Call real Dev Agent to apply fix
        wf._apply_fix(vi.get("file"), strategy)
        attempted_fixes.append((db_issue, vi))
        
    # Verify batched validation
    assert len(attempted_fixes) >= 1
    re_issues = wf._call_validator()
    
    # Update statuses
    for db_issue, vi in attempted_fixes:
        still_broken = any(
            r.get("file") == vi.get("file") and r.get("check") == vi.get("check")
            for r in re_issues
        )
        if still_broken:
            job_db.update_validation_issue_status(db_issue["id"], "failed", error="Failed to fix")
        else:
            job_db.update_validation_issue_status(db_issue["id"], "completed")
            
    # Assertions
    # Check that the syntax error was resolved
    assert not any(r.get("file") == "app.py" and r.get("check") == "integration" for r in re_issues)
    
    # Check that the file was successfully modified (e.g. contains colon now)
    content = app_file.read_text(encoding="utf-8")
    assert "def run():" in content


@pytest.mark.e2e
@pytest.mark.slow
@pytest.mark.timeout(1200)
def test_validation_remediation_twenty_issues_e2e(tmp_path, monkeypatch):
    """Seed 20 broken source files and verify real LLM remediation fixes them in a batch, profiling speed."""
    import time
    
    # Isolated DB and Workspace
    db_path = tmp_path / "e2e_remediation_20.db"
    job_db = JobDatabase(db_path)
    
    job_id = str(uuid.uuid4())
    workspace = tmp_path / "workspace" / f"job-{job_id}"
    workspace.mkdir(parents=True)
    
    # 1. Seed 20 broken source files
    num_files = 20
    files = []
    for i in range(1, num_files + 1):
        filename = f"app_{i}.py"
        file_path = workspace / filename
        file_path.write_text(f"def run_{i}()\n    print('hello {i}')\n", encoding="utf-8")
        files.append((filename, file_path))
        
    # Create job in database
    job_db.create_job(job_id, "20 broken CLIs", str(workspace))
    
    # Load config and initialize workflow
    config = ConfigLoader.load()
    wf = SoftwareDevWorkflow(
        project_id=job_id,
        workspace_path=workspace,
        vision="20 broken CLIs",
        config=config,
        job_db=job_db,
    )
    
    # Initialize real agents using default/simple backstories
    wf.tech_stack = "Python CLI"
    wf.tech_architect_agent = TechArchitectAgent(
        custom_backstory="You are a principal software architect who provides single-paragraph fix strategies.",
        workspace_path=workspace,
    )
    wf.dev_agent = DevAgent(
        custom_backstory="You are an expert developer who modifies code files using replace_file_content.",
        workspace_path=workspace,
        config=config,
    )
    
    # 2. Call validator to identify the issues
    start_validate_time = time.time()
    issues = wf._call_validator()
    validate_duration = time.time() - start_validate_time
    print(f"\nInitial validation took: {validate_duration:.2f} seconds")
    
    assert len(issues) == num_files
    
    # Register the validation issues in DB
    for vi in issues:
        job_db.create_validation_issue(
            issue_id=str(uuid.uuid4()),
            job_id=job_id,
            check_name=vi["check"],
            severity=vi.get("severity", "error"),
            file_path=vi["file"],
            line_number=vi.get("line"),
            description=vi.get("description", ""),
        )
        
    # 3. Trigger remediation loop via real LLM calls
    auto_fixed = wf._auto_fix_issues(issues)
    auto_fixed_files = {i.get("file") for i in auto_fixed}
    
    attempted_fixes = []
    
    start_remediation_time = time.time()
    for vi in issues:
        if vi.get("file") in auto_fixed_files:
            continue
        if vi.get("severity") != "error":
            continue
            
        pending = job_db.get_pending_validation_issues(job_id)
        matching = [p for p in pending if p["file_path"] == vi.get("file") and p["check_name"] == vi.get("check")]
        if not matching:
            continue
        db_issue = matching[0]
        
        job_db.update_validation_issue_status(db_issue["id"], "running")
        
        # Call real Tech Architect to define strategy
        strategy = wf._get_fix_strategy(vi)
        assert strategy, "Failed to produce strategy from Tech Architect"
        
        job_db.update_validation_issue_status(db_issue["id"], "running", fix_strategy=strategy)
        
        # Call real Dev Agent to apply fix
        wf._apply_fix(vi.get("file"), strategy)
        attempted_fixes.append((db_issue, vi))
        
    # Verify batched validation
    assert len(attempted_fixes) == num_files
    
    start_batch_validate_time = time.time()
    re_issues = wf._call_validator()
    batch_validate_duration = time.time() - start_batch_validate_time
    print(f"Batched validation after fixes took: {batch_validate_duration:.2f} seconds")
    
    # Update statuses
    for db_issue, vi in attempted_fixes:
        still_broken = any(
            r.get("file") == vi.get("file") and r.get("check") == vi.get("check")
            for r in re_issues
        )
        if still_broken:
            job_db.update_validation_issue_status(db_issue["id"], "failed", error="Failed to fix")
        else:
            job_db.update_validation_issue_status(db_issue["id"], "completed")
            
    remediation_duration = time.time() - start_remediation_time
    print(f"Total remediation loop (20 issues, real LLMs) took: {remediation_duration:.2f} seconds")
    
    # Assertions
    # Check that at least some errors are resolved (LLM non-determinism may cause some to fail)
    assert len(re_issues) < num_files
    
    # Assert that at least 10 files were successfully fixed
    num_fixed = num_files - len(re_issues)
    assert num_fixed >= 10, f"Expected at least 10 files to be fixed, but only got {num_fixed}"
    
    # Check that the successfully fixed files actually contain the colon
    still_broken_files = {r.get("file") for r in re_issues}
    for filename, file_path in files:
        if filename not in still_broken_files:
            content = file_path.read_text(encoding="utf-8")
            idx = filename.split("_")[1].split(".")[0]
            assert f"def run_{idx}():" in content


