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


# ---------------------------------------------------------------------------
# DeepSeek channel-token stripping tests (TDD — must pass after fix)
# ---------------------------------------------------------------------------

# Real patterns observed in production logs from DeepSeek R1 Distill 14B running
# in simple mode.  The model bleeds its internal multi-turn channel tokens into
# the response instead of outputting the required JSON array.

_DS_COMMENTARY = "<|channel|>commentary<|message|>We need to output the tool action.<|end|>"

_DS_TOOL_CALL = (
    "<|start|>assistant<|channel|>commentary to=code_search <|constrain|>json"
    '<|message|>{"pattern":"FirebaseService","workspace":"/app/workspace/abc"}<|call|>'
)

_DS_ANALYSIS_CALL = (
    "<|start|>assistant<|channel|>analysis to=code_structure code"
    '<|message|>{"workspace":"/app/workspace/abc","lang":"python"}<|call|>'
)

_DS_PREAMBLE_THEN_JSON = (
    "<|channel|>commentary<|message|>We will now output the action.<|end|>\n"
    "[{\"file_path\": \"src/services/OCRService.js\","
    " \"content\": \"const OCRService = {}; export default OCRService;\"}]"
)

_DS_START_THEN_JSON = (
    "<|start|>assistant<|channel|>commentary to=file_writer <|constrain|>json"
    "<|message|>we will create the file<|end|>\n"
    "[{\"file_path\": \"src/App.js\", \"content\": \"import React from 'react';\"}]"
)


class TestDeepSeekTokenStripping:
    """_clean_response must strip DeepSeek channel tokens so the JSON parser can find the array."""

    def test_pure_commentary_token_yields_empty(self):
        from llamaindex_crew.utils.output_parser import extract_files_from_response
        entries, strategy = extract_files_from_response(_DS_COMMENTARY, target_file_path="any.js")
        # No JSON in this response — should return nothing (not crash)
        assert entries == []

    def test_pure_tool_call_token_yields_empty(self):
        from llamaindex_crew.utils.output_parser import extract_files_from_response
        entries, _ = extract_files_from_response(_DS_TOOL_CALL, target_file_path="any.js")
        assert entries == []

    def test_channel_preamble_then_json_extracts_file(self):
        """Commentary token followed by valid JSON — JSON must be extracted."""
        from llamaindex_crew.utils.output_parser import extract_files_from_response
        entries, strategy = extract_files_from_response(
            _DS_PREAMBLE_THEN_JSON, target_file_path="src/services/OCRService.js"
        )
        assert len(entries) == 1, f"Expected 1 entry, got {entries}"
        assert entries[0]["file_path"] == "src/services/OCRService.js"
        assert strategy == "json"

    def test_start_token_preamble_then_json_extracts_file(self):
        """<|start|>assistant token followed by JSON — JSON must be extracted."""
        from llamaindex_crew.utils.output_parser import extract_files_from_response
        entries, strategy = extract_files_from_response(
            _DS_START_THEN_JSON, target_file_path="src/App.js"
        )
        assert len(entries) == 1, f"Expected 1 entry, got {entries}"
        assert entries[0]["file_path"] == "src/App.js"
        assert strategy == "json"

    def test_analysis_token_yields_empty_no_json(self):
        """Analysis channel call with no JSON — should not crash, return empty."""
        from llamaindex_crew.utils.output_parser import extract_files_from_response
        entries, _ = extract_files_from_response(_DS_ANALYSIS_CALL, target_file_path="src/x.py")
        assert entries == []

    def test_multiple_channel_tokens_then_json(self):
        """Multiple commentary tokens before the JSON array — all stripped."""
        from llamaindex_crew.utils.output_parser import extract_files_from_response
        response = (
            "<|channel|>commentary<|message|>Inspecting repo.<|end|>\n"
            "<|channel|>commentary<|message|>Now writing file.<|end|>\n"
            '[{"file_path": "src/utils/helpers.js", "content": "export const noop = () => {};"}]'
        )
        entries, strategy = extract_files_from_response(
            response, target_file_path="src/utils/helpers.js"
        )
        assert len(entries) == 1
        assert entries[0]["file_path"] == "src/utils/helpers.js"
        assert strategy == "json"

    def test_channel_token_before_code_fence(self):
        """Commentary token before a code fence — code fence fallback must still work."""
        from llamaindex_crew.utils.output_parser import extract_files_from_response
        response = (
            "<|channel|>commentary<|message|>Will create the file now.<|end|>\n"
            "```javascript\n"
            "const x = 1;\nexport default x;\n"
            "```"
        )
        entries, strategy = extract_files_from_response(
            response, target_file_path="src/config.js"
        )
        assert len(entries) == 1
        assert entries[0]["file_path"] == "src/config.js"
        assert strategy == "code_fence"

    def test_clean_json_response_unaffected(self):
        """A response without any channel tokens must parse exactly as before."""
        from llamaindex_crew.utils.output_parser import extract_files_from_response
        response = '[{"file_path": "src/index.js", "content": "console.log(1);"}]'
        entries, strategy = extract_files_from_response(
            response, target_file_path="src/index.js"
        )
        assert len(entries) == 1
        assert strategy == "json"

    def test_looks_like_raw_agent_dump_detects_channel_tokens(self):
        """looks_like_raw_agent_dump should flag responses that are purely channel tokens."""
        from llamaindex_crew.utils.output_parser import looks_like_raw_agent_dump
        assert looks_like_raw_agent_dump(_DS_COMMENTARY) is True
        assert looks_like_raw_agent_dump(_DS_TOOL_CALL) is True
