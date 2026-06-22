"""Tests for simple-mode structured output parsing."""
from llamaindex_crew.utils.output_parser import is_valid_file_path, parse_file_list


def test_rejects_gherkin_text_masquerading_as_filename():
    bogus_path = (
        "features/data_transformation_n_as_a_data_professional_n_i_want_to_apply_"
        "built_in_transformations_to_my_data_n_so_that_i_can_clean_enrich_and_"
        "summarize_datasets_efficiently.feature"
    )
    assert is_valid_file_path(bogus_path) is False


def test_parse_file_list_rejects_long_fenced_header():
    response = (
        "assistant: We need to output JSON array with files: requirements.md\n"
        f"```features/data_transformation_n_as_a_data_professional_n_i_want_"
        f"to_apply_built_in_transformations.feature\n"
        "Feature: Data Transformation\n"
        "Scenario: filter rows\n"
        "```"
    )
    assert parse_file_list(response) == []


def test_parse_file_list_accepts_valid_json():
    response = """[
      {"file_path": "requirements.md", "content": "# Requirements\\n"},
      {"file_path": "features/data.feature", "content": "Feature: X\\n"}
    ]"""
    files = parse_file_list(response)
    assert len(files) == 2
    assert files[0]["file_path"] == "requirements.md"


def test_parse_file_list_accepts_valid_fenced_block():
    response = """Here are the files:

```requirements.md
# Requirements
- CLI tool
```

```features/data_transformation.feature
Feature: Data Transformation
  Scenario: filter rows
```
"""
    files = parse_file_list(response)
    assert len(files) == 2
    assert files[1]["file_path"] == "features/data_transformation.feature"


def test_parse_file_list_strips_assistant_prefix_before_json():
    response = """assistant: [
      {"file_path": "user_stories.md", "content": "# Stories\\n"}
    ]"""
    files = parse_file_list(response)
    assert len(files) == 1
    assert files[0]["file_path"] == "user_stories.md"


def test_parse_file_list_salvages_complete_objects_from_truncated_array():
    response = """assistant: [
  {
    "file_path": "requirements.md",
    "content": "# High-Level Requirements\\n\\n- Lightweight CLI tool.\\n"
  },
  {
    "file_path": "user_stories.md",
    "content": "# User Stories\\n\\n- As a user I want"""
    files = parse_file_list(response)
    assert len(files) == 2
    assert files[0]["file_path"] == "requirements.md"
    assert "Lightweight CLI" in files[0]["content"]
    assert files[1]["file_path"] == "user_stories.md"


def test_parse_file_list_handles_unescaped_newlines_in_content():
    response = '''assistant: [
  {
    "file_path": "requirements.md",
    "content": "# High-Level Requirements

- Lightweight CLI tool"
  }
]'''
    files = parse_file_list(response)
    assert len(files) == 1
    assert files[0]["file_path"] == "requirements.md"
    assert "Lightweight CLI" in files[0]["content"]

    response = (
        'assistant: [\n'
        '  {\n'
        '    "file_path": "requirements.md",\n'
        '    "content": "# High-Level Requirements\\n\\n- Lightweight CLI tool.\\n"\n'
        '  }\n'
        ']'
    )
    files = parse_file_list(response)
    assert len(files) == 1
    assert files[0]["file_path"] == "requirements.md"


def test_extract_code_fence_for_single_file_target():
    from llamaindex_crew.utils.output_parser import extract_files_from_response

    response = """assistant: I need to create the init file.

```python
\"\"\"Data processing CLI package.\"\"\"
__version__ = "0.1.0"
```
"""
    entries, strategy = extract_files_from_response(
        response, target_file_path="src/__init__.py",
    )
    assert strategy == "code_fence"
    assert len(entries) == 1
    assert entries[0]["file_path"] == "src/__init__.py"
    assert "__version__" in entries[0]["content"]


def test_is_valid_gherkin_feature_rejects_stub():
    from llamaindex_crew.utils.output_parser import is_valid_gherkin_feature

    assert is_valid_gherkin_feature(
        "Details for features like asset categorization and lifecycle management."
    ) is False
    assert is_valid_gherkin_feature(
        "Feature: Asset tracking\n  Scenario: Register asset\n"
        "    Given an admin\n    When they add an asset\n    Then it is listed\n"
    ) is True


def test_extract_file_writer_pseudo_call():
    from llamaindex_crew.utils.output_parser import extract_files_from_response

    body = (
        "# IT Asset App\n\n## Components\n- Asset DocType\n- Allocation\n\n"
        "## Data Model\n- IT Asset\n- Depreciation\n\n## UI\n- Workspace\n"
    )
    escaped = body.replace("\n", "\\n")
    response = (
        "assistant: file_writer(file_path='design_spec.md', content="
        f"'{escaped}')"
    )
    entries, strategy = extract_files_from_response(response, target_file_path="design_spec.md")
    assert strategy == "file_writer"
    assert len(entries) == 1
    assert entries[0]["file_path"] == "design_spec.md"
    assert "Asset DocType" in entries[0]["content"]


def test_rejects_raw_agent_dump_as_design_spec():
    from llamaindex_crew.utils.output_parser import is_valid_design_spec, looks_like_raw_agent_dump

    raw = "assistant: file_writer(file_path='design_spec.md', content='# Title\\n')"
    assert looks_like_raw_agent_dump(raw) is True
    assert is_valid_design_spec(raw) is False
