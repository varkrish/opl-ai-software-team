"""Tests for skill-first wiring prompt injection and Frappe contract normalize."""
from __future__ import annotations

from llamaindex_crew.utils.wiring_prompt import (
    build_neutral_wiring_fallback,
    build_stack_wiring_example,
    compose_framework_reference_with_wiring,
    detect_stack_family,
    infer_app_slug,
)
from llamaindex_crew.utils.wiring_contract import normalize_frappe_wiring_contract


class TestDetectStackFamily:
    def test_frappe_vision(self):
        assert detect_stack_family("Create a Frappe App for Movie screening") == "frappe"

    def test_frappe_manifest(self):
        assert (
            detect_stack_family(
                "build something",
                stack_manifest={"chosen_stack": ["frappe", "python"], "skills_query": "frappe"},
            )
            == "frappe"
        )

    def test_go_vision(self):
        assert detect_stack_family("Build a Go HTTP sandbox API with go.mod") == "go"

    def test_python_vision(self):
        assert detect_stack_family("Build a FastAPI calculator in Python") == "python"


class TestInferAppSlug:
    def test_movie_ticketing_from_vision(self):
        slug = infer_app_slug("Create a Frappe App for Movie screening and ticketing")
        assert "movie" in slug or "ticket" in slug or "screen" in slug
        assert "app_name" not in slug

    def test_never_app_name(self):
        assert infer_app_slug("app_name") != "app_name"


class TestSkillFirstWiringCompose:
    def test_skills_are_authoritative_no_hardcoded_stack_tree(self):
        text = compose_framework_reference_with_wiring(
            "[Skill: frappe-app-scaffold]\nUse hooks.py and modules.txt under the app package",
            vision="Create a Frappe App for invoicing",
        )
        assert "AUTHORITATIVE" in text
        assert "[Skill: frappe-app-scaffold]" in text
        assert "hooks.py" in text
        # No hardcoded competing Go / nested Frappe tree
        assert "github.com/example" not in text
        assert "internal/api" not in text
        assert "STACK WIRING EXAMPLE" not in text

    def test_empty_skills_uses_neutral_fallback(self):
        text = compose_framework_reference_with_wiring(
            "",
            vision="Create a Frappe App for Movie screening and ticketing",
        )
        assert "No indexed skill matched" in text or "WIRING RULES" in text
        assert "github.com/example" not in text
        assert "internal/api" not in text

    def test_neutral_fallback_has_no_stack_specific_tree(self):
        ex = build_neutral_wiring_fallback(vision="Build a Go sandbox API")
        assert "github.com/" not in ex or "Do not copy" in ex
        assert 'packages["internal/api"]' not in ex
        # Deprecated alias still works
        assert "WIRING RULES" in build_stack_wiring_example("go", vision="Build a Go sandbox API")


class TestNormalizeFrappeWiring:
    def test_rewrites_app_name_and_go_module(self):
        contract = {
            "version": 1,
            "module": "github.com/example/movie_app",
            "language": "python",
            "packages": {
                "app_name/app_name/movie/doctype/movie": {
                    "files": ["app_name/app_name/movie/doctype/movie/movie.py"],
                    "owns": ["Movie"],
                },
                "internal/api": {
                    "files": ["internal/api/handler.go"],
                    "owns": [],
                },
            },
            "symbols": {},
            "deps": [],
        }
        out = normalize_frappe_wiring_contract(
            contract,
            vision="Create a Frappe App for Movie screening and ticketing",
            stack_manifest={"chosen_stack": ["frappe"], "skills_query": "frappe"},
        )
        assert out is not None
        assert "github.com" not in out["module"]
        assert out["language"] == "python"
        assert "internal/api" not in out["packages"]
        assert any("hooks.py" in f for pkg in out["packages"].values() for f in pkg.get("files") or [])
        assert not any(k.startswith("app_name") for k in out["packages"])

    def test_keeps_flat_scaffold_without_forcing_nested(self):
        flat_only = {
            "version": 1,
            "module": "movie_ticketing",
            "language": "python",
            "packages": {
                "movie_ticketing": {
                    "files": [
                        "movie_ticketing/__init__.py",
                        "movie_ticketing/hooks.py",
                        "movie_ticketing/modules.txt",
                    ],
                    "owns": ["hooks"],
                },
            },
            "symbols": {},
            "deps": [],
        }
        out = normalize_frappe_wiring_contract(
            flat_only,
            vision="Create a Frappe app named movie_ticketing",
            stack_manifest={"chosen_stack": ["frappe"]},
        )
        all_files = [
            f for pkg in out["packages"].values() for f in (pkg.get("files") or [])
        ]
        assert "movie_ticketing/hooks.py" in all_files
        assert "movie_ticketing/movie_ticketing/hooks.py" not in all_files
        assert "movie_ticketing/movie_ticketing" not in out["packages"]

    def test_flat_on_disk_satisfies_nested_declaration(self, tmp_path):
        from llamaindex_crew.utils.wiring_contract import missing_declared_source_files

        (tmp_path / "movie_ticketing").mkdir()
        (tmp_path / "movie_ticketing" / "hooks.py").write_text("# hooks\n", encoding="utf-8")
        (tmp_path / "movie_ticketing" / "__init__.py").write_text("", encoding="utf-8")
        contract = {
            "packages": {
                "movie_ticketing/movie_ticketing": {
                    "files": [
                        "movie_ticketing/movie_ticketing/hooks.py",
                        "movie_ticketing/movie_ticketing/__init__.py",
                    ],
                },
            },
        }
        assert missing_declared_source_files(contract, tmp_path) == []

    def test_noop_for_non_frappe(self):
        contract = {
            "version": 1,
            "module": "github.com/example/sandbox",
            "language": "go",
            "packages": {"internal/api": {"files": ["internal/api/h.go"], "owns": []}},
            "symbols": {},
            "deps": [],
        }
        out = normalize_frappe_wiring_contract(
            contract,
            vision="Build a Go sandbox API",
            stack_manifest={"chosen_stack": ["go"]},
        )
        assert out["module"] == "github.com/example/sandbox"
        assert "internal/api" in out["packages"]
