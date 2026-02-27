import pytest
from pathlib import Path
from unittest.mock import MagicMock
from llamaindex_crew.agents.migration_agent import MigrationExecutionAgent

def test_migration_execution_agent_large_file_prompt():
    # Setup
    workspace = Path("/tmp/fake_ws")
    agent = MigrationExecutionAgent(workspace, "test-proj")
    
    file_path = "LargeFile.java"
    # Create content that is "large" (> 30,000 chars)
    file_content = "public class LargeFile {\n" + "    // some content\n" * 2000 + "}\n"
    issues = [{"id": "issue-1", "title": "Move to Jakarta", "migration_hint": "Replace javax with jakarta"}]
    
    # Execute
    prompt = agent.build_prompt(
        file_path=file_path,
        file_content=file_content,
        issues=issues,
        migration_goal="Jakarta migration",
        truncated=True
    )
    
    # Verify
    assert "file_line_replacer" in prompt
    assert "1:" in prompt  # Check for line numbering
    assert "2000:" in prompt
    assert "COMPLETE" not in prompt or "file_writer" not in prompt  # Rules should be relaxed for large files
