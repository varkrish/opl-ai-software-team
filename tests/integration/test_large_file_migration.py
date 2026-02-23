import pytest
import os
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch
from crew_studio.migration.runner import run_migration

def test_large_file_migration_integration(tmp_path):
    # Setup workspace
    ws = tmp_path
    (ws / "docs").mkdir()
    (ws / "src/main/java/com/example").mkdir(parents=True)
    
    # Create a large Java file (> 60KB)
    large_file_path = "src/main/java/com/example/LargeFile.java"
    large_content = "package com.example;\n\npublic class LargeFile {\n" + \
                    "    public void method() {\n        System.out.println(\"Hello\");\n    }\n" * 1500 + \
                    "}\n"
    (ws / large_file_path).write_text(large_content)
    
    # Create a mock MTA issues.json
    report_path = "docs/issues.json"
    report_content = [
        {
            "applicationId": "test-app",
            "issues": {
                "mandatory": [
                    {
                        "ruleId": "replace-println",
                        "name": "Replace println",
                        "effort": {"type": "Trivial"},
                        "affectedFiles": [
                            {
                                "description": "Replace println with logger",
                                "files": [{"fileName": "com.example.LargeFile"}]
                            }
                        ]
                    }
                ]
            }
        }
    ]
    import json
    (ws / report_path).write_text(json.dumps(report_content))
    
    # Mock JobDatabase
    mock_db = MagicMock()
    
    # Patch MigrationExecutionAgent to simulate successful execution using file_line_replacer
    with patch("crew_studio.migration.runner.git_snapshot"), \
         patch("crew_studio.migration.runner.load_migration_rules", return_value=""), \
         patch("llamaindex_crew.agents.migration_agent.MigrationExecutionAgent") as MockAgent:
        
        agent_instance = MockAgent.return_value
        
        # Simulate agent using file_line_replacer via the tool system
        def simulate_run(*args, **kwargs):
            # In a real run, the agent would call the tool. 
            # Here we just verify the runner passes the 'truncated=True' flag
            assert kwargs.get("truncated") is True
            # Simulate the side effect of the agent replacing a line
            new_content = large_content.replace("System.out.println", "logger.info")
            (ws / large_file_path).write_text(new_content)
        
        agent_instance.run.side_effect = simulate_run
        
        # Run migration
        run_migration(
            job_id="test-job",
            workspace_path=str(ws),
            migration_goal="Replace println",
            report_path=report_path,
            migration_notes=None,
            job_db=mock_db
        )
        
    # Verify results
    updated_content = (ws / large_file_path).read_text()
    assert "logger.info" in updated_content
    assert "System.out.println" not in updated_content
    assert len(updated_content) > 60000
    
    # Verify DB calls
    assert mock_db.create_migration_issue.called
    assert mock_db.update_migration_issue_status.called
