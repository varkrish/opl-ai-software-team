import pytest
from llamaindex_crew.tools.file_tools import patch_file_content

def test_patch_file_content_exact_match(tmp_path):
    file_path = tmp_path / "test.txt"
    file_path.write_text("def hello():\n    print('world')\n")
    
    diff_blocks = """<<<< SEARCH
def hello():
    print('world')
====
def hello(name):
    print(f'hello {name}')
>>>> REPLACE"""
    
    result = patch_file_content("test.txt", diff_blocks, workspace_path=str(tmp_path))
    assert "Successfully applied 1 block(s)" in result
    
    content = file_path.read_text()
    assert "def hello(name):" in content
    assert "print(f'hello {name}')" in content

def test_patch_file_content_multiple_blocks(tmp_path):
    file_path = tmp_path / "test.txt"
    file_path.write_text("a\nb\nc\nd\ne\n")
    
    diff_blocks = """<<<< SEARCH
b
====
B
>>>> REPLACE
<<<< SEARCH
d
====
D
>>>> REPLACE"""
    
    result = patch_file_content("test.txt", diff_blocks, workspace_path=str(tmp_path))
    assert "Successfully applied 2 block(s)" in result
    
    content = file_path.read_text()
    assert content == "a\nB\nc\nD\ne\n"

def test_patch_file_content_missing_search(tmp_path):
    file_path = tmp_path / "test.txt"
    file_path.write_text("a\nb\nc\n")
    
    diff_blocks = """<<<< SEARCH
x
====
y
>>>> REPLACE"""
    
    result = patch_file_content("test.txt", diff_blocks, workspace_path=str(tmp_path))
    assert "Error: SEARCH block not found" in result
    
    content = file_path.read_text()
    assert content == "a\nb\nc\n"

def test_patch_file_content_whitespace_fallback(tmp_path):
    # LLMs sometimes output trailing spaces that aren't in the file
    file_path = tmp_path / "test.txt"
    file_path.write_text("def func():\n    pass\n")
    
    diff_blocks = """<<<< SEARCH
def func(): 
    pass 
====
def func(a):
    pass
>>>> REPLACE"""
    
    result = patch_file_content("test.txt", diff_blocks, workspace_path=str(tmp_path))
    assert "Successfully applied" in result
    
    content = file_path.read_text()
    assert "def func(a):" in content
