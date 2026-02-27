import pytest
from pathlib import Path
from llamaindex_crew.tools.file_tools import file_line_replacer

def test_file_line_replacer_single_line(tmp_path):
    # Setup
    workspace = tmp_path
    fpath = "test.txt"
    content = "Line 1\nLine 2\nLine 3"
    (workspace / fpath).write_text(content)
    
    # Execute: Replace Line 2
    new_line = "New Line 2"
    result = file_line_replacer(fpath, 2, 2, new_line, workspace_path=str(workspace))
    
    # Verify
    assert "Successfully" in result
    updated_content = (workspace / fpath).read_text()
    assert updated_content == "Line 1\nNew Line 2\nLine 3"

def test_file_line_replacer_multi_line(tmp_path):
    # Setup
    workspace = tmp_path
    fpath = "test.txt"
    content = "Line 1\nLine 2\nLine 3\nLine 4"
    (workspace / fpath).write_text(content)
    
    # Execute: Replace Line 2 to 3
    new_content = "New 2\nNew 3"
    result = file_line_replacer(fpath, 2, 3, new_content, workspace_path=str(workspace))
    
    # Verify
    updated_content = (workspace / fpath).read_text()
    assert updated_content == "Line 1\nNew 2\nNew 3\nLine 4"

def test_file_line_replacer_invalid_range(tmp_path):
    # Setup
    workspace = tmp_path
    fpath = "test.txt"
    content = "Line 1\nLine 2"
    (workspace / fpath).write_text(content)
    
    # Execute: Range out of bounds
    result = file_line_replacer(fpath, 1, 5, "New", workspace_path=str(workspace))
    assert "Error" in result or "Invalid" in result
