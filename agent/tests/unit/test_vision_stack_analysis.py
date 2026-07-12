"""Tests for vision-driven stack inference and overreach detection."""
import pytest

from llamaindex_crew.utils.vision_stack_analysis import (
    _manifest_forbidden_violation,
    build_stack_selection_brief,
    decide_solutioning_path,
    detect_stack_overreach,
    infer_capability_profile,
)
from llamaindex_crew.workflows.software_dev_workflow import _check_vision_coherence

MAP_VISION = (
    "Create a simple HTML page showing Asia Pacific region map with SVG, "
    "country labels and a colour legend"
)

FRAPPE_DESIGN = """
# Design Specification for Asia-Pacific Map Frappe App
Uses Frappe Page, MapConfig DocType, hooks.py, frappe.client.get_single, MariaDB.
"""

FRAPPE_VISION = "Build a Frappe invoicing app with customer and invoice DocTypes"

SPRING_VISION = (
    "Build a REST API for employee directory. Use Spring Boot with PostgreSQL."
)
PYTHON_ARTIFACT = "We will use Python with FastAPI and SQLite for the employee API."

CLIENT_MANIFEST = {
    "path": "fast",
    "delivery_surface": "client_deliverable",
    "complexity": "minimal",
    "chosen_stack": ["html", "css", "svg"],
    "forbidden_tiers": ["application_server", "database", "cms_platform"],
    "rationale": "Simple client page",
    "skills_query": "vanilla html svg accessibility",
}


class TestInferCapabilityProfile:
    def test_map_vision_is_client_deliverable(self):
        profile = infer_capability_profile(MAP_VISION)
        assert profile.delivery_surface == "client_deliverable"
        assert profile.needs_server_runtime is False
        assert profile.complexity == "minimal"
        assert profile.explicit_technologies == []
        assert profile.suggested_path == "fast"

    def test_frappe_vision_needs_platform(self):
        profile = infer_capability_profile(FRAPPE_VISION)
        assert "frappe" in profile.explicit_technologies
        assert profile.delivery_surface == "platform_app"
        assert profile.needs_server_runtime is True
        assert profile.suggested_path == "full"

    def test_api_vision_needs_server(self):
        profile = infer_capability_profile(SPRING_VISION)
        assert profile.needs_api is True
        assert profile.needs_persistence is True
        assert profile.needs_server_runtime is True
        assert profile.suggested_path == "full"

    def test_markup_payload_detected(self):
        vision = "<!DOCTYPE html><html><body><svg></svg></body></html>"
        profile = infer_capability_profile(vision)
        assert profile.has_client_surface is True
        assert profile.delivery_surface == "client_deliverable"


class TestDecideSolutioningPath:
    def test_override_fast_wins(self):
        assert decide_solutioning_path(FRAPPE_VISION, solutioning_path="fast") == "fast"

    def test_override_full_wins_on_map(self):
        assert decide_solutioning_path(MAP_VISION, solutioning_path="full") == "full"

    def test_adaptive_map_is_fast(self):
        assert decide_solutioning_path(MAP_VISION, solutioning_path="adaptive") == "fast"

    def test_adaptive_frappe_is_full(self):
        assert decide_solutioning_path(FRAPPE_VISION, solutioning_path="adaptive") == "full"

    def test_adaptive_spring_is_full(self):
        assert decide_solutioning_path(SPRING_VISION, solutioning_path="adaptive") == "full"

    def test_default_missing_path_is_full(self):
        assert decide_solutioning_path(MAP_VISION, solutioning_path=None) == "full"
        assert decide_solutioning_path(MAP_VISION) == "full"


class TestManifestForbiddenTier:
    MANIFEST = {
        "chosen_stack": ["react", "next.js", "express", "node.js"],
        "forbidden_tiers": ["database", "cms_platform"],
    }

    def test_negated_database_mention_is_not_violation(self):
        artifact = (
            "Map uses OpenStreetMap without requiring a proprietary mapping service "
            "or additional database tier."
        )
        assert _manifest_forbidden_violation(artifact, self.MANIFEST) is None

    def test_formatting_does_not_trigger_orm_marker(self):
        artifact = "Linting/formatting uses ESLint + Prettier for code quality."
        assert _manifest_forbidden_violation(artifact, self.MANIFEST) is None

    def test_real_database_still_violates(self):
        artifact = "Persistence: PostgreSQL database with SQLAlchemy ORM."
        reason = _manifest_forbidden_violation(artifact, self.MANIFEST)
        assert reason is not None
        assert "database" in reason


class TestDetectStackOverreach:
    def test_frappe_for_map_vision_is_overreach(self):
        reason = detect_stack_overreach(MAP_VISION, FRAPPE_DESIGN)
        assert reason is not None
        assert "frappe" in reason.lower()

    def test_frappe_vision_with_frappe_design_ok(self):
        design = "Frappe app with invoice DocType and hooks.py"
        assert detect_stack_overreach(FRAPPE_VISION, design) is None

    def test_spring_vision_rejects_fastapi_artifact(self):
        reason = detect_stack_overreach(SPRING_VISION, PYTHON_ARTIFACT)
        assert reason is not None

    def test_manifest_overreach_rejects_frappe_tech_stack(self):
        tech_stack = "# Stack\nFramework: Frappe\nDatabase: MariaDB\nhooks.py"
        reason = detect_stack_overreach(
            MAP_VISION, tech_stack, stack_manifest=CLIENT_MANIFEST
        )
        assert reason is not None
        assert "frappe" in reason.lower() or "forbidden" in reason.lower()


class TestVisionCoherenceWithStack:
    def test_map_vision_fails_frappe_tech_stack(self):
        tech_stack = "# Stack\nFramework: Frappe\nDatabase: MariaDB\nhooks.py"
        assert _check_vision_coherence(MAP_VISION, tech_stack, "tech_stack.md") is False

    def test_map_vision_passes_minimal_stack(self):
        tech_stack = (
            "# Stack\nVanilla HTML, CSS, SVG, ES modules\n"
            "index.html with Asia Pacific region map, country labels, colour legend"
        )
        assert _check_vision_coherence(MAP_VISION, tech_stack, "tech_stack.md") is True

    def test_keyword_overlap_alone_does_not_pass_frappe(self):
        """Keyword overlap used to pass incorrectly — stack check must fail."""
        assert _check_vision_coherence(MAP_VISION, FRAPPE_DESIGN, "design_spec.md") is False


class TestStackSelectionBrief:
    def test_brief_mentions_minimal_stack_when_unnamed(self):
        brief = build_stack_selection_brief(MAP_VISION)
        assert "MINIMAL" in brief
        assert "client_deliverable" in brief

    def test_brief_respects_named_frappe(self):
        brief = build_stack_selection_brief(FRAPPE_VISION)
        assert "frappe" in brief.lower()
