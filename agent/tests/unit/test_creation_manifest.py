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

    def test_scaffolding_only_manifest_rejected(self):
        """Dockerfile/tests without app source must not soft-pass completeness."""
        entries = [
            {"path": "pyproject.toml", "description": "meta", "tier": "supplementary"},
            {"path": "Dockerfile", "description": "container", "tier": "supplementary"},
            {"path": "README.md", "description": "docs", "tier": "supplementary"},
            {"path": "tests/test_auth.py", "description": "tests", "tier": "supplementary"},
            {"path": "tests/__init__.py", "description": "init", "tier": "injected"},
        ]
        result = validate_manifest_completeness(
            entries,
            design_spec="Task management REST API with Keycloak",
            solution_spec="FastAPI service with OpenTelemetry and Keycloak auth",
        )
        assert result["valid"] is False
        from llamaindex_crew.utils.wiring_contract import implementation_manifest_paths
        assert implementation_manifest_paths(entries) == []

    def test_ensure_package_file_paths_from_python_owns(self):
        """Owns-only wiring_patch must synthesize concrete .py paths under module."""
        from llamaindex_crew.utils.wiring_contract import (
            ensure_package_file_paths,
            files_from_contract,
            build_creation_manifest,
            implementation_manifest_paths,
        )

        contract = {
            "version": 1,
            "module": "project",
            "language": "python",
            "packages": {
                "project": {"files": [], "owns": ["project.main"]},
                "project.api": {"files": [], "owns": ["project.api.handlers"]},
                "project.service": {"files": [], "owns": ["project.service.task_service"]},
                "project.domain": {"files": [], "owns": ["project.domain.models"]},
            },
            "symbols": {},
            "deps": [],
            "_meta": {"source": "jq-patch", "enforcement": "relaxed"},
        }
        filled = ensure_package_file_paths(contract)
        assert "project/main.py" in filled["packages"]["project"]["files"]
        assert any(p.endswith(".py") for p in filled["packages"]["project.api"]["files"])
        paths = {e["path"] for e in files_from_contract(filled)}
        assert "project/main.py" in paths
        manifest = build_creation_manifest(
            filled,
            [
                {"path": "pyproject.toml", "description": "meta"},
                {"path": "tests/test_tasks.py", "description": "tests"},
            ],
            design_spec="Task management REST API",
        )
        impl = implementation_manifest_paths(manifest)
        assert "project/main.py" in impl
        assert len(impl) >= 3
        assert validate_manifest_completeness(
            manifest,
            design_spec="Task management REST API with auth",
            solution_spec="FastAPI Keycloak OpenTelemetry",
        )["valid"] is True

    def test_string_symbol_wiring_patch_is_accepted(self):
        """Bare string symbol values must coerce — not discard the whole patch."""
        from llamaindex_crew.utils.wiring_contract import (
            extract_wiring_contract_from_specs,
            files_from_contract,
        )

        tech = """
## File Structure
```
project/
├── main.py
└── tests/test_calc.py
```
<wiring_patch>
.module = "project"
| .language = "python"
| .packages["project.api"].files = ["project/api/handlers.py"]
| .packages["project.api"].owns = ["project.api.calculate"]
| .packages["project.service"].files = ["project/service/calculator.py"]
| .packages["project.service"].owns = ["project.service.add"]
| .packages["project.entrypoint"].files = ["main.py"]
| .symbols["project.service.add"] = "(int, int) -> int"
| .symbols["project.api.calculate"] = "(int, int, string) -> int|float"
| .deps["project.api"] = ["project.service"]
| .deps["project.entrypoint"] = ["project.api"]
</wiring_patch>
"""
        contract = extract_wiring_contract_from_specs(
            "", "", language_hint="python", tech_stack=tech
        )
        assert (contract.get("_meta") or {}).get("source") == "jq-patch"
        assert contract.get("module") == "project"
        assert isinstance(contract["symbols"]["project.service.add"], dict)
        assert "signature" in contract["symbols"]["project.service.add"]
        paths = {e["path"] for e in files_from_contract(contract)}
        assert "project/api/handlers.py" in paths
        assert "project/service/calculator.py" in paths
        assert "main.py" in paths

    def test_owns_only_wiring_patch_is_accepted(self):
        """jq patch with owns but no .files must synthesize and keep source=jq-patch."""
        from llamaindex_crew.utils.wiring_contract import (
            extract_wiring_contract_from_specs,
            implementation_manifest_paths,
            build_creation_manifest,
        )

        tech = """
## File Structure
```
project/
└── tests/test_tasks.py
```
<wiring_patch>
.module = "project"
| .language = "python"
| .packages["project"].owns = ["project.main"]
| .packages["project.api"].owns = ["handlers"]
| .packages["project.service"].owns = ["task_service"]
</wiring_patch>
"""
        contract = extract_wiring_contract_from_specs(
            "", "", language_hint="python", tech_stack=tech
        )
        assert (contract.get("_meta") or {}).get("source") == "jq-patch"
        assert "project/main.py" in (contract["packages"]["project"]["files"] or [])
        impl = implementation_manifest_paths(build_creation_manifest(contract, [], "API"))
        assert "project/main.py" in impl
        assert any("api" in p for p in impl)

    @pytest.mark.parametrize(
        "language,module,packages,must_contain",
        [
            (
                "python",
                "calc",
                {"calc": {"files": [], "owns": ["calc.main", "Calculator"]}},
                ["calc/main.py"],
            ),
            (
                "java",
                "com.example.calc",
                {
                    "com.example.calc": {
                        "files": [],
                        "owns": ["Calculator", "Main"],
                    }
                },
                ["com/example/calc/Calculator.java"],
            ),
            (
                "go",
                "github.com/example/calc",
                {
                    "internal/calculator": {
                        "files": [],
                        "owns": ["Add", "Subtract"],
                    },
                    "cmd/calc": {"files": [], "owns": ["main"]},
                },
                ["internal/calculator/add.go"],
            ),
            (
                "html",
                "calculator-page",
                {"web": {"files": [], "owns": ["index", "appScript"]}},
                ["index.html", "styles.css", "app.js"],
            ),
            (
                "javascript",
                "calc-cli",
                {"src": {"files": [], "owns": ["main", "add"]}},
                ["src/index.js"],
            ),
        ],
        ids=["python", "java", "golang", "html", "nodejs"],
    )
    def test_ensure_package_file_paths_multi_language(
        self, language, module, packages, must_contain
    ):
        from llamaindex_crew.utils.wiring_contract import (
            ensure_package_file_paths,
            files_from_contract,
            implementation_manifest_paths,
            build_creation_manifest,
        )

        contract = {
            "version": 1,
            "module": module,
            "language": language,
            "packages": packages,
            "symbols": {},
            "deps": [],
            "_meta": {"source": "jq-patch", "enforcement": "relaxed"},
        }
        filled = ensure_package_file_paths(contract)
        paths = {e["path"] for e in files_from_contract(filled)}
        for expected in must_contain:
            assert expected in paths, f"{language}: missing {expected} in {sorted(paths)}"
        impl = implementation_manifest_paths(build_creation_manifest(filled, [], "calc"))
        for expected in must_contain:
            # HTML helpers like styles.css count as impl via web delivery suffixes
            assert expected in impl or expected.endswith((".css",)), (
                f"{language}: {expected} not in impl {impl}"
            )
        result = validate_manifest_completeness(
            build_creation_manifest(filled, [], "Simple calculator"),
            design_spec="Simple minimal calculator",
            solution_spec="Minimal calculator",
        )
        assert result["valid"] is True, result


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
