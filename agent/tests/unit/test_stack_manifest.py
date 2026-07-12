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


TRAVEL_VISION = (
    "Create an AI-powered Travel Planner UI called Voyager for families "
    "to plan trips easily. Web UI with itinerary generation."
)

TRAVEL_SPEC_WITH_REDIS = """\
# Solution Specification for Voyager

## Technology Stack
| Layer | Technology |
|-------|------------|
| Front-end | React 18 + Next.js 14 |
| Backend | Express (Node.js) |
| Caching | Upstash Redis |

## Caching Strategy (Redis)
1. AI Cache — Key: `ai:itinerary:{hash}` → JSON, TTL 15 min.
2. Flight/Hotel Cache — Key: `amadeus:flights:{query}`, TTL 30 min.

## Non-Goals
- No on-premise self-hosted Redis.
- No complex AI agents (LangChain).
"""

TRAVEL_SPEC_WITH_POSTGRES = """\
# Solution Specification for Voyager

## Technology Stack
| Layer | Technology |
|-------|------------|
| Front-end | React 18 + Next.js 14 |
| Backend | Express (Node.js) |
| Database | PostgreSQL with Prisma ORM |

Persistence layer stores user itineraries.
"""

TRAVEL_SPEC_NO_DATA = """\
# Solution Specification for Voyager

## Technology Stack
| Layer | Technology |
|-------|------------|
| Front-end | React 18 + Next.js 14 |
| Backend | Express (Node.js) |

No persistence or caching required. All data is ephemeral.
"""


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

    def test_redis_in_spec_unlocks_database_tier(self, tmp_path: Path):
        """Approved spec with Redis/Upstash must NOT forbid the database tier."""
        profile = infer_capability_profile(TRAVEL_VISION)
        manifest = write_stack_manifest_from_solution_spec(
            TRAVEL_VISION, profile, tmp_path, spec_text=TRAVEL_SPEC_WITH_REDIS
        )
        assert "database" not in manifest["forbidden_tiers"], (
            "database tier should be unlocked when approved spec mentions Redis/caching"
        )
        assert manifest["needs_persistence"] is True

    def test_postgres_in_spec_unlocks_database_tier(self, tmp_path: Path):
        """Approved spec with PostgreSQL must NOT forbid the database tier."""
        profile = infer_capability_profile(TRAVEL_VISION)
        manifest = write_stack_manifest_from_solution_spec(
            TRAVEL_VISION, profile, tmp_path, spec_text=TRAVEL_SPEC_WITH_POSTGRES
        )
        assert "database" not in manifest["forbidden_tiers"], (
            "database tier should be unlocked when approved spec mentions PostgreSQL"
        )
        assert manifest["needs_persistence"] is True

    def test_no_data_spec_keeps_database_forbidden(self, tmp_path: Path):
        """Spec without data/cache mentions should keep database forbidden for client visions."""
        profile = infer_capability_profile(MAP_VISION)
        manifest = write_stack_manifest_from_solution_spec(
            MAP_VISION, profile, tmp_path, spec_text=TRAVEL_SPEC_NO_DATA
        )
        assert "database" in manifest["forbidden_tiers"], (
            "database tier should stay forbidden when spec has no data signals"
        )
        assert manifest["needs_persistence"] is False
