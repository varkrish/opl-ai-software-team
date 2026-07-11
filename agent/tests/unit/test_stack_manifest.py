"""Tests for stack_manifest write/read and fast-path stack decision (real filesystem)."""
import json
from pathlib import Path

import pytest

from llamaindex_crew.utils.vision_stack_analysis import infer_capability_profile
from llamaindex_crew.workflows.solutioning_loop import (
    read_stack_manifest,
    run_fast_stack_decision,
    write_stack_manifest,
    write_stack_manifest_from_solution_spec,
)

MAP_VISION = (
    "Create a simple HTML page showing Asia Pacific region map with SVG, "
    "country labels and a colour legend"
)

FRAPPE_VISION = "Build a Frappe invoicing app with customer and invoice DocTypes"

REQUIRED_KEYS = {
    "path",
    "delivery_surface",
    "complexity",
    "chosen_stack",
    "forbidden_tiers",
    "rationale",
    "skills_query",
}


class TestWriteReadStackManifest:
    def test_write_stack_manifest_creates_required_keys(self, tmp_path: Path):
        data = {
            "path": "fast",
            "delivery_surface": "client_deliverable",
            "complexity": "minimal",
            "chosen_stack": ["html", "css", "svg"],
            "forbidden_tiers": ["application_server", "database", "cms_platform"],
            "rationale": "Simple client page",
            "skills_query": "vanilla html svg accessibility",
        }
        path = write_stack_manifest(tmp_path, data)
        assert path.exists()
        assert path.name == "stack_manifest.json"
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert REQUIRED_KEYS.issubset(loaded.keys())

    def test_round_trip_read_equals_written(self, tmp_path: Path):
        data = {
            "path": "full",
            "delivery_surface": "platform_app",
            "complexity": "complex",
            "chosen_stack": ["frappe"],
            "forbidden_tiers": [],
            "rationale": "Named Frappe stack",
            "skills_query": "frappe doctype hooks",
            "source": "user",
        }
        write_stack_manifest(tmp_path, data)
        assert read_stack_manifest(tmp_path) == data

    def test_read_missing_returns_none(self, tmp_path: Path):
        assert read_stack_manifest(tmp_path) is None


class TestRunFastStackDecision:
    def test_fast_path_for_map_vision(self, tmp_path: Path):
        profile = infer_capability_profile(MAP_VISION)
        result = run_fast_stack_decision(MAP_VISION, profile, tmp_path)

        manifest = read_stack_manifest(tmp_path)
        assert manifest is not None
        assert manifest["path"] == "fast"
        assert "frappe" not in [s.lower() for s in manifest["chosen_stack"]]
        for tier in ("application_server", "database", "cms_platform"):
            assert tier in manifest["forbidden_tiers"]
        assert result.spec_path.exists()
        assert result.spec_path.read_text(encoding="utf-8").strip()
        assert (tmp_path / "solution_spec.md").exists()
        assert len((tmp_path / "solution_spec.md").read_text(encoding="utf-8").strip()) > 20

    def test_fast_path_respects_named_tech(self, tmp_path: Path):
        vision = "Create a simple React widget showing a colour legend"
        profile = infer_capability_profile(vision)
        run_fast_stack_decision(vision, profile, tmp_path)
        manifest = read_stack_manifest(tmp_path)
        assert "react" in [s.lower() for s in manifest["chosen_stack"]]


class TestFullPathManifestFromSpec:
    def test_full_path_writer_from_approved_solution_spec(self, tmp_path: Path):
        spec = (
            "# Solution Specification\n\n"
            "## Chosen stack\n"
            "- Frappe Framework\n"
            "- MariaDB\n\n"
            "Build an invoicing DocType app.\n"
        )
        (tmp_path / "solution_spec.md").write_text(spec, encoding="utf-8")
        profile = infer_capability_profile(FRAPPE_VISION)
        manifest = write_stack_manifest_from_solution_spec(
            FRAPPE_VISION, profile, tmp_path, spec_text=spec
        )
        assert manifest["path"] == "full"
        assert any("frappe" in s.lower() for s in manifest["chosen_stack"])
        assert read_stack_manifest(tmp_path)["path"] == "full"
