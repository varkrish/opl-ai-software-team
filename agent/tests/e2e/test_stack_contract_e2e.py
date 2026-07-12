"""
E2E tests for stack contract scenarios (Voyager-style full pipeline contracts).

These tests exercise manifest derivation, forbidden-tier validation, and
vision coherence using real workspace files — no LLM calls required.

Regression coverage for job f84a5d82-style failures:
  - Approved solution_spec with Redis must unlock database tier in manifest
  - Tech Architect prose saying "without database tier" must not false-fail
  - Real PostgreSQL in tech_stack must still fail when manifest forbids database
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from llamaindex_crew.utils.vision_stack_analysis import (
    _manifest_forbidden_violation,
    detect_stack_overreach,
    infer_capability_profile,
)
from llamaindex_crew.workflows.solutioning_loop import (
    read_stack_manifest,
    run_fast_stack_decision,
    write_stack_manifest_from_solution_spec,
)

FIXTURES = Path(__file__).parent / "fixtures" / "stack_contract"

MAP_VISION = (
    "Create a simple HTML page showing Asia Pacific region map with SVG, "
    "country labels and a colour legend"
)

TRAVEL_VISION = (
    "Create an AI-powered Travel Planner UI called Voyager for families "
    "to plan trips easily. Web UI with itinerary generation."
)

GOOGLE_HTML_VISION = (
    "Create a simple HTML page that mocks a Google search homepage. "
    "Pure HTML and CSS only, single index.html."
)


def _read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _lock_manifest_from_spec(workspace: Path, vision: str, spec_text: str) -> dict:
    """Simulate post-solutioning manifest lock from an approved solution_spec."""
    spec_path = workspace / "solution_spec.md"
    spec_path.write_text(spec_text, encoding="utf-8")
    profile = infer_capability_profile(vision)
    return write_stack_manifest_from_solution_spec(
        vision, profile, workspace, spec_text=spec_text
    )


def _validate_tech_stack(workspace: Path, vision: str, tech_stack_text: str) -> bool:
    """Run stack-manifest coherence gate (same check as Tech Architect pass 2)."""
    tech_path = workspace / "tech_stack.md"
    tech_path.write_text(tech_stack_text, encoding="utf-8")
    manifest = read_stack_manifest(workspace)
    return detect_stack_overreach(
        vision, tech_stack_text, stack_manifest=manifest
    ) is None


@pytest.mark.e2e
class TestVoyagerStackContractE2E:
    """Full-path Voyager scenario: Redis in approved spec → manifest → tech_stack."""

    def test_redis_spec_unlocks_database_tier_in_manifest(self, e2e_workspace: Path):
        spec = _read_fixture("voyager_spec_with_redis.md")
        manifest = _lock_manifest_from_spec(e2e_workspace, TRAVEL_VISION, spec)

        assert manifest["path"] == "full"
        assert "database" not in manifest["forbidden_tiers"], (
            "Approved spec with Upstash Redis must unlock database tier"
        )
        assert manifest["needs_persistence"] is True
        assert "react" in [s.lower() for s in manifest["chosen_stack"]]
        assert read_stack_manifest(e2e_workspace) == manifest

    def test_negated_database_prose_passes_tech_architect_gate(self, e2e_workspace: Path):
        """Regression: job f84a5d82 failed on 'without database tier' prose."""
        spec = _read_fixture("voyager_spec_with_redis.md")
        _lock_manifest_from_spec(e2e_workspace, TRAVEL_VISION, spec)

        tech_stack = _read_fixture("voyager_tech_stack_negated_database.md")
        assert _validate_tech_stack(e2e_workspace, TRAVEL_VISION, tech_stack), (
            "Negated database mentions and redisClient.ts must pass when "
            "manifest unlocked database tier from approved spec"
        )

    def test_real_postgres_fails_when_manifest_forbids_database(self, e2e_workspace: Path):
        """When manifest still forbids database, real PostgreSQL must fail."""
        manifest = {
            "path": "full",
            "chosen_stack": ["react", "next.js", "express", "node.js"],
            "forbidden_tiers": ["database", "cms_platform"],
        }
        (e2e_workspace / "stack_manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )

        tech_stack = _read_fixture("voyager_tech_stack_real_database.md")
        reason = detect_stack_overreach(
            TRAVEL_VISION, tech_stack, stack_manifest=manifest
        )
        assert reason is not None
        assert "database" in reason.lower()
        assert not _validate_tech_stack(e2e_workspace, TRAVEL_VISION, tech_stack)

    def test_formatting_does_not_trigger_orm_false_positive(self, e2e_workspace: Path):
        manifest = {
            "chosen_stack": ["react", "next.js", "express", "node.js"],
            "forbidden_tiers": ["database"],
        }
        tech_stack = _read_fixture("voyager_tech_stack_negated_database.md")
        assert _manifest_forbidden_violation(tech_stack, manifest) is None


@pytest.mark.e2e
class TestFastPathStackContractE2E:
    """Fast-path scenarios: simple HTML / client deliverables."""

    def test_google_html_fast_manifest_forbids_database(self, e2e_workspace: Path):
        profile = infer_capability_profile(GOOGLE_HTML_VISION)
        run_fast_stack_decision(GOOGLE_HTML_VISION, profile, e2e_workspace)
        manifest = read_stack_manifest(e2e_workspace)

        assert manifest is not None
        assert manifest["path"] == "fast"
        assert "database" in manifest["forbidden_tiers"]
        assert "application_server" in manifest["forbidden_tiers"]

    def test_map_html_tech_stack_passes_minimal_manifest(self, e2e_workspace: Path):
        profile = infer_capability_profile(MAP_VISION)
        run_fast_stack_decision(MAP_VISION, profile, e2e_workspace)

        tech_stack = (
            "# Stack\nVanilla HTML, CSS, SVG\n"
            "index.html with Asia Pacific region map, country labels, colour legend\n"
            "<tech_stack>\nproject-root/\n└── index.html\n</tech_stack>"
        )
        assert _validate_tech_stack(e2e_workspace, MAP_VISION, tech_stack)

    def test_frappe_in_tech_stack_fails_map_vision(self, e2e_workspace: Path):
        profile = infer_capability_profile(MAP_VISION)
        run_fast_stack_decision(MAP_VISION, profile, e2e_workspace)

        tech_stack = "# Stack\nFramework: Frappe\nDatabase: MariaDB\nhooks.py"
        assert not _validate_tech_stack(e2e_workspace, MAP_VISION, tech_stack)


@pytest.mark.e2e
class TestAdaptiveRoutingE2E:
    """Adaptive capability routing for realistic visions (no LLM)."""

    def test_map_vision_suggests_fast_path(self):
        profile = infer_capability_profile(MAP_VISION)
        assert profile.suggested_path == "fast"
        assert profile.delivery_surface == "client_deliverable"

    def test_travel_vision_full_path_requires_explicit_profile(self):
        """Short travel vision infers minimal/fast; full pipeline is user-selected."""
        from llamaindex_crew.utils.vision_stack_analysis import decide_solutioning_path

        profile = infer_capability_profile(TRAVEL_VISION)
        assert profile.has_client_surface
        # Vision alone may suggest fast (no persistence named in short text)
        assert decide_solutioning_path(TRAVEL_VISION, solutioning_path="full") == "full"
        assert decide_solutioning_path(TRAVEL_VISION, solutioning_path="adaptive") in (
            "fast",
            "full",
        )

    def test_google_html_is_client_deliverable(self):
        profile = infer_capability_profile(GOOGLE_HTML_VISION)
        assert profile.delivery_surface == "client_deliverable"
        assert profile.complexity == "minimal"


@pytest.mark.e2e
@pytest.mark.api
class TestCapabilityProfileApiE2E:
    """API accepts capability_profile without 422 (regression for UI selector)."""

    @pytest.fixture
    def client(self):
        import sys
        root = Path(__file__).resolve().parent.parent.parent
        sys.path.insert(0, str(root))
        sys.path.insert(0, str(root / "agent"))
        sys.path.insert(0, str(root / "agent" / "src"))
        from crew_studio.llamaindex_web_app import app
        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c

    def _create(self, client, body: dict) -> dict:
        resp = client.post("/api/jobs", json=body, content_type="application/json")
        assert resp.status_code == 201, resp.get_json()
        return resp.get_json()

    def test_fast_profile_accepts_string(self, client):
        data = self._create(client, {
            "vision": GOOGLE_HTML_VISION,
            "backend": "opl-ai-team",
            "capability_profile": "fast",
        })
        assert "job_id" in data

    def test_full_profile_accepts_string(self, client):
        data = self._create(client, {
            "vision": TRAVEL_VISION,
            "backend": "opl-ai-team",
            "capability_profile": "full",
        })
        assert "job_id" in data

    def test_auto_omits_profile(self, client):
        data = self._create(client, {
            "vision": MAP_VISION,
            "backend": "opl-ai-team",
        })
        assert "job_id" in data
