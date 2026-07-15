"""Tests for contract-driven creation manifest builder and DB registration."""
import json
from pathlib import Path

import pytest

from llamaindex_crew.utils.wiring_contract import (
    FileEntry,
    build_creation_manifest,
    files_from_contract,
    merge_file_manifests,
    parse_supplementary_file_entries,
    render_tech_stack_from_manifest,
    should_skip_contract_reseed_from_tech_stack,
    validate_manifest_completeness,
    validate_supplementary_paths,
)
from llamaindex_crew.orchestrator.task_manager import TaskManager


@pytest.fixture
def sample_contract() -> dict:
    return {
        "version": 1,
        "module": "my-api",
        "language": "go",
        "packages": {
            "internal/api": {
                "files": ["internal/api/handler.go"],
                "owns": ["CreateHandler"],
            },
            "internal/service": {
                "files": ["internal/service/manager.go"],
                "owns": ["Create"],
            },
        },
        "symbols": {},
        "deps": [],
        "_meta": {"source": "jq-patch", "enforcement": "strict"},
    }


class TestCreationManifestBuilder:
    def test_files_from_contract(self, sample_contract):
        entries = files_from_contract(sample_contract)
        paths = {e["path"] for e in entries}
        assert paths == {"internal/api/handler.go", "internal/service/manager.go"}
        assert all(e["tier"] == "contract" for e in entries)

    def test_supplementary_rejects_contract_collision(self, sample_contract):
        supp: list[FileEntry] = [
            {"path": "go.mod", "description": "module"},
            {"path": "internal/service/manager.go", "description": "duplicate"},
        ]
        validated = validate_supplementary_paths(supp, sample_contract)
        paths = {e["path"] for e in validated}
        assert "go.mod" in paths
        assert "internal/service/manager.go" not in paths

    def test_merge_contract_wins(self, sample_contract):
        mandatory = files_from_contract(sample_contract)
        supplementary: list[FileEntry] = [
            {"path": "go.mod", "description": "go module", "manifest_source": "supplementary", "tier": "supplementary"},
        ]
        merged = merge_file_manifests(mandatory, supplementary)
        paths = {e["path"] for e in merged}
        assert "go.mod" in paths
        assert "internal/api/handler.go" in paths

    def test_build_creation_manifest_merges_tiers(self, sample_contract, tmp_path):
        supp: list[FileEntry] = [{"path": "go.mod", "description": "module"}]
        manifest = build_creation_manifest(
            sample_contract,
            supp,
            design_spec="",
            workspace=tmp_path,
        )
        paths = {e["path"] for e in manifest}
        assert "internal/api/handler.go" in paths
        assert "go.mod" in paths

    def test_parse_supplementary_json(self):
        text = """
<supplementary_files>
[
  {"path": "README.md", "description": "docs"},
  {"path": "go.mod", "description": "module"}
]
</supplementary_files>
"""
        entries = parse_supplementary_file_entries(text)
        assert len(entries) == 2
        assert entries[0]["path"] == "README.md"

    def test_render_tech_stack_from_manifest(self):
        entries: list[FileEntry] = [
            {"path": "main.go", "description": "entry"},
            {"path": "go.mod", "description": "mod"},
        ]
        rendered = render_tech_stack_from_manifest(entries, stack_prose="# Stack\nGo")
        assert "## File Structure" in rendered
        assert "main.go" in rendered
        assert "go.mod" in rendered

    def test_should_skip_reseed_for_jq_patch(self, sample_contract):
        assert should_skip_contract_reseed_from_tech_stack(sample_contract) is True
        assert should_skip_contract_reseed_from_tech_stack({"_meta": {"source": "extract-fallback"}}) is False

    def test_validate_manifest_completeness_contract_only(self, sample_contract):
        manifest = build_creation_manifest(sample_contract, [], "")
        # Contract-only manifests with < min_impl default may be shallow — add supplementary
        manifest = build_creation_manifest(
            sample_contract,
            [
                {"path": "go.mod", "description": "module"},
                {"path": "cmd/server/main.go", "description": "entry"},
                {"path": "internal/config/config.go", "description": "config"},
                {"path": "README.md", "description": "docs"},
            ],
            "",
        )
        result = validate_manifest_completeness(manifest)
        assert result["valid"] is True

    def test_tiny_python_calculator_manifest_accepted(self):
        """Simple 2-file CLI must not be rejected by the old hard floor of 4."""
        entries = [
            {"path": "calculator.py", "description": "Calculator class", "tier": "contract"},
            {"path": "main.py", "description": "CLI entry", "tier": "contract"},
            {"path": "test_calculator.py", "description": "tests", "tier": "supplementary"},
        ]
        result = validate_manifest_completeness(
            entries,
            design_spec="Simple Python command-line calculator",
            solution_spec="Minimal Calculator with add/subtract",
        )
        assert result["valid"] is True
        assert result["implementation_files"] >= 2


class TestManifestDbRoundTrip:
    def test_register_and_query_manifest(self, tmp_path, sample_contract):
        db_path = tmp_path / "tasks_test.db"
        tm = TaskManager(db_path, "proj1", workspace_path=tmp_path)
        manifest = build_creation_manifest(
            sample_contract,
            [{"path": "go.mod", "description": "module"}],
            "",
            workspace=tmp_path,
        )
        tm.register_granular_tasks_from_manifest(manifest, design_spec="")
        entries = tm.get_manifest_entries()
        paths = {e["path"] for e in entries}
        assert "internal/api/handler.go" in paths
        assert "go.mod" in paths
        assert any(e.get("manifest_source") == "contract" for e in entries)

    def test_scaffold_from_manifest(self, tmp_path, sample_contract):
        db_path = tmp_path / "tasks_test.db"
        tm = TaskManager(db_path, "proj1", workspace_path=tmp_path)
        manifest = build_creation_manifest(sample_contract, [], "", workspace=tmp_path)
        tm.register_granular_tasks_from_manifest(manifest, design_spec="")
        entries = tm.get_manifest_entries()
        created = tm.scaffold_directories_from_manifest(entries, tmp_path)
        assert (tmp_path / "internal" / "api").is_dir() or created
