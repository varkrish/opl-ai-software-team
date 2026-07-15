"""Unit tests for wiring_contract.py binding utility."""
import shutil
from unittest.mock import patch

import pytest
from pathlib import Path
from llamaindex_crew.utils.wiring_contract import (
    WiringContractError,
    validate_wiring_contract,
    write_wiring_contract,
    load_wiring_contract,
    all_declared_files,
    package_for_file,
    deps_for_package,
    slice_for_file,
    tech_stack_violates_contract,
    import_prefix,
    extract_wiring_contract_from_specs,
    extract_files_with_descriptions_from_tech_stack,
    is_strict_wiring_enforcement,
    parse_emitted_wiring_contract,
    parse_emitted_wiring_patch,
    apply_wiring_patch,
    resolve_jq_bin,
    _build_path_only_contract_from_specs,
    infer_module_from_specs,
    package_has_boundary_keywords,
    unauthorized_package_prefix_for_path,
    extract_import_paths,
    import_references_declared_package,
    import_matches_module_root,
    collect_unauthorized_sibling_packages,
    reconcile_workspace_against_contract,
    missing_declared_source_files,
    package_has_domain_keywords,
    symbol_key,
    contract_has_planned_apis,
    normalize_symbol_keys,
    extract_planned_interfaces_from_specs,
    strengthen_contract_from_specs,
    _is_weak_module_name,
)

@pytest.fixture
def path_seed_contract():
    return {
        "version": 1,
        "module": "unknown",
        "language": "unknown",
        "packages": {
            "internal/api": {"files": ["internal/api/handler.go"], "owns": []},
            "internal/podman": {"files": ["internal/podman/podman.go"], "owns": []},
        },
        "symbols": {},
        "deps": [],
    }


@pytest.fixture
def sample_contract_data():
    return {
        "version": 1,
        "module": "sandbox-api",
        "language": "go",
        "api_contract": "api_contract.yaml",
        "packages": {
            "internal/api": {
                "owns": ["CreateSandbox", "ExecuteCommand"],
                "files": ["internal/api/handler.go", "internal/api/request.go"]
            },
            "internal/podman": {
                "owns": ["RunContainer"],
                "files": ["internal/podman/podman.go"]
            }
        },
        "symbols": {
            "RunContainer": {
                "package": "internal/podman",
                "signature": "func RunContainer(ctx context.Context, image string) error",
                "exports": ["RunContainer"]
            }
        },
        "deps": [
            {"from": "internal/api", "to": "internal/podman"}
        ]
    }

class TestWiringContract:
    def test_validate_wiring_contract_success(self, sample_contract_data):
        normalized = validate_wiring_contract(sample_contract_data)
        assert normalized["module"] == "sandbox-api"
        assert normalized["version"] == 1

    def test_validate_wiring_contract_missing_fields(self):
        with pytest.raises(WiringContractError, match="Missing required field: 'version'"):
            validate_wiring_contract({})

        with pytest.raises(WiringContractError, match="Missing required field: 'module'"):
            validate_wiring_contract({"version": 1})

        with pytest.raises(WiringContractError, match="Missing required field: 'packages'"):
            validate_wiring_contract({"version": 1, "module": "test"})

    def test_validate_wiring_contract_invalid_package_files(self):
        data = {
            "version": 1,
            "module": "test",
            "packages": {
                "internal/api": {
                    "files": "not-a-list"
                }
            }
        }
        with pytest.raises(WiringContractError, match="field 'files' must be a list of strings"):
            validate_wiring_contract(data)

    def test_write_and_load_contract_roundtrip(self, sample_contract_data, tmp_path):
        written_path = write_wiring_contract(tmp_path, sample_contract_data)
        assert written_path.exists()
        
        loaded = load_wiring_contract(tmp_path)
        assert loaded is not None
        assert loaded["module"] == "sandbox-api"
        assert loaded["version"] == 1
        assert "internal/api" in loaded["packages"]

    def test_load_missing_contract(self, tmp_path):
        assert load_wiring_contract(tmp_path) is None

    def test_all_declared_files(self, sample_contract_data):
        files = all_declared_files(sample_contract_data)
        assert files == {"internal/api/handler.go", "internal/api/request.go", "internal/podman/podman.go"}

    def test_package_for_file(self, sample_contract_data):
        assert package_for_file(sample_contract_data, "internal/api/handler.go") == "internal/api"
        assert package_for_file(sample_contract_data, "internal/api/v1/nested.go") == "internal/api"
        assert package_for_file(sample_contract_data, "internal/podman/podman.go") == "internal/podman"
        assert package_for_file(sample_contract_data, "cmd/main.go") is None

    def test_deps_for_package(self, sample_contract_data):
        assert deps_for_package(sample_contract_data, "internal/api") == ["internal/podman"]
        assert deps_for_package(sample_contract_data, "internal/podman") == []

    def test_slice_for_file(self, sample_contract_data):
        w_slice = slice_for_file(sample_contract_data, "internal/api/handler.go")
        assert "MODULE: sandbox-api" in w_slice
        assert "CURRENT PACKAGE: internal/api" in w_slice
        assert "CreateSandbox, ExecuteCommand" in w_slice
        assert "DEPENDS ON PACKAGES: internal/podman" in w_slice
        assert "func RunContainer" in w_slice

    def test_import_prefix(self, sample_contract_data):
        assert import_prefix(sample_contract_data) == "sandbox-api"

    def test_tech_stack_violates_contract(self, sample_contract_data):
        # 1. Matching/valid paths
        valid_paths = [
            "internal/api/handler.go",
            "internal/podman/podman.go",
            "go.mod",
            "README.md",
        ]
        assert tech_stack_violates_contract(sample_contract_data, "", valid_paths) is None
        # Generic root-level files without any directory segment are always ok
        assert tech_stack_violates_contract(sample_contract_data, "", ["custom-tool.cfg", "Makefile"]) is None

        # 2. Path not under declared prefixes (strict enforcement only)
        invalid_paths = [
            "internal/api/handler.go",
            "internal/storage/cache.go",
        ]
        assert tech_stack_violates_contract(sample_contract_data, "", invalid_paths) is None
        violation = tech_stack_violates_contract(
            sample_contract_data, "", invalid_paths, strict=True
        )
        assert violation is not None
        assert "internal/storage" in violation

        # 3. Sibling package competing handler rejection
        competing_paths = [
            "internal/api/handler.go",
            "internal/handlers/request.go"
        ]
        violation = tech_stack_violates_contract(sample_contract_data, "", competing_paths)
        assert violation is not None
        assert "Competing package 'internal/handlers' was introduced" in violation

    def test_tech_stack_competing_package_src_layout(self):
        contract = {
            "version": 1,
            "module": "my-app",
            "packages": {
                "src/api": {
                    "owns": ["CreateOrder"],
                    "files": ["src/api/routes.py"],
                },
                "src/services": {
                    "owns": ["OrderService"],
                    "files": ["src/services/orders.py"],
                },
            },
            "symbols": {},
            "deps": [],
        }
        competing_paths = ["src/api/routes.py", "src/handlers/legacy.py"]
        violation = tech_stack_violates_contract(contract, "", competing_paths)
        assert violation is not None
        assert "src/handlers" in violation

    def test_infer_module_from_pyproject(self):
        spec = '[project]\nname = "billing-app"\n'
        assert infer_module_from_specs(spec, "") == "billing-app"

    def test_collect_unauthorized_sibling_packages_src_layout(self, tmp_path):
        declared = {"src/api", "src/services"}
        (tmp_path / "src" / "api").mkdir(parents=True)
        (tmp_path / "src" / "services").mkdir(parents=True)
        rogue = tmp_path / "src" / "handlers"
        rogue.mkdir()
        (rogue / "extra.py").write_text("pass\n", encoding="utf-8")
        found = collect_unauthorized_sibling_packages(tmp_path, declared)
        assert found == ["src/handlers"]

    def test_reconcile_workspace_python_import_prefix(self, tmp_path):
        contract = {
            "version": 1,
            "module": "my-app",
            "packages": {
                "src/api": {"files": ["src/api/routes.py"], "owns": []},
                "src/services": {"files": ["src/services/orders.py"], "owns": []},
            },
            "symbols": {},
            "deps": [],
        }
        api_dir = tmp_path / "src" / "api"
        api_dir.mkdir(parents=True)
        (api_dir / "routes.py").write_text(
            "from other_project.src.services import orders\n",
            encoding="utf-8",
        )
        issues = reconcile_workspace_against_contract(contract, tmp_path)
        assert any("wrong module import prefix" in i["description"] for i in issues)

    def test_extract_import_paths_multilang(self):
        assert "my/app/pkg" in extract_import_paths('import "my/app/pkg"\n', ".go")
        assert "src.services.orders" in extract_import_paths(
            "from src.services.orders import OrderService\n", ".py"
        )
        assert "./components/Button" in extract_import_paths(
            'import Button from "./components/Button"\n', ".tsx"
        )

    def test_extract_wiring_contract_from_specs(self):
        solution_spec = """
# Solution Spec
module github.com/example/my-module

Here is the file structure:
- internal/api/handler.go
- internal/podman/podman.go
- cmd/main.go
"""
        design_spec = """
# Design Spec
## internal/podman
Interface contracts:
func RunContainer(ctx context.Context, image string) error
"""
        contract = extract_wiring_contract_from_specs(solution_spec, design_spec)
        assert contract["version"] == 1
        assert contract["module"] == "github.com/example/my-module"
        assert "internal/api" in contract["packages"]
        assert "internal/podman" in contract["packages"]
        # Prose interfaces strengthen path seed
        assert "internal/podman.RunContainer" in contract["symbols"]
        assert "RunContainer" in contract["packages"]["internal/podman"]["owns"]
        assert contract["deps"] == []
        assert contract["_meta"]["source"] == "spec-interfaces"
        assert not is_strict_wiring_enforcement(contract)

    def test_workflow_wiring_allowlist_and_reconcile(self, tmp_path):
        from unittest.mock import MagicMock
        
        from llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow
        
        class DummyWorkflow:
            def __init__(self):
                self._wiring_contract = None
                self.workspace_path = tmp_path
                self.task_manager = MagicMock()
                self.design_spec = ""
                self.tech_stack = ""

            def _solution_contract_active(self):
                return False

            def _load_solution_spec_text(self):
                return ""

            _build_wiring_allowlist = SoftwareDevWorkflow._build_wiring_allowlist
            _run_wiring_reconcile_pass = SoftwareDevWorkflow._run_wiring_reconcile_pass
            _enrich_wiring_after_codegen = SoftwareDevWorkflow._enrich_wiring_after_codegen
            _is_strict_wiring_enforcement = SoftwareDevWorkflow._is_strict_wiring_enforcement
            _ensure_wiring_contract_locked = SoftwareDevWorkflow._ensure_wiring_contract_locked
            
        workflow = DummyWorkflow()
        
        # Test 1: No contract
        assert workflow._build_wiring_allowlist() is None
        assert workflow._run_wiring_reconcile_pass() == {"pass": True, "issues": []}
        
        # Test 2: With contract
        contract = {
            "version": 1,
            "module": "my-module",
            "packages": {
                "internal/api": {
                    "files": ["internal/api/handler.go", "routes.go"],
                    "owns": ["CreateSandbox"]
                },
                "internal/podman": {
                    "files": ["internal/podman/podman.go"],
                    "owns": ["RunContainer"]
                }
            },
            "symbols": {
                "CreateSandbox": {"package": "internal/api"},
                "RunContainer": {"package": "internal/podman"}
            },
            "deps": [
                {"pkg": "internal/api", "deps": ["internal/podman"]}
            ]
        }
        
        workflow._wiring_contract = contract
        task_manager = MagicMock()
        task_manager.get_registered_file_paths.return_value = {"cmd/main.go"}
        workflow.task_manager = task_manager
        
        # Create some files in root workspace to verify generic root files inclusion
        (tmp_path / "Makefile").write_text("all:\n", encoding="utf-8")
        (tmp_path / "go.mod").write_text("module my-module\n", encoding="utf-8")
        
        allowed = workflow._build_wiring_allowlist()
        assert allowed is not None
        # Assert that paths are correct and do not double to internal/api/internal/api/handler.go
        assert "internal/api/internal/api/handler.go" not in allowed
        assert "internal/api/handler.go" in allowed
        assert "internal/podman/podman.go" in allowed
        assert "cmd/main.go" in allowed
        assert "Makefile" in allowed
        assert "go.mod" in allowed
        assert "api_contract.yaml" in allowed
        
        # Test 3: Wiring reconciliation pass
        # Create a file under an unauthorized package directory
        unauthorized_dir = tmp_path / "internal" / "handlers"
        unauthorized_dir.mkdir(parents=True, exist_ok=True)
        (unauthorized_dir / "unauthorized.go").write_text("package handlers\n", encoding="utf-8")
        
        # Create a file with a wrong Go import prefix
        api_dir = tmp_path / "internal" / "api"
        api_dir.mkdir(parents=True, exist_ok=True)
        bad_go_file = api_dir / "handler.go"
        bad_go_file.write_text('package api\nimport "github.com/example/my-module/internal/podman"\n', encoding="utf-8")
        (api_dir / "routes.go").write_text("package api\n", encoding="utf-8")
        (tmp_path / "routes.go").write_text("package main\n", encoding="utf-8")

        podman_dir = tmp_path / "internal" / "podman"
        podman_dir.mkdir(parents=True, exist_ok=True)
        (podman_dir / "podman.go").write_text("package podman\n", encoding="utf-8")
        
        reconcile_result = workflow._run_wiring_reconcile_pass()
        assert reconcile_result["pass"]  # relaxed contract — issues are informational only
        issues = reconcile_result["issues"]
        
        # Verify unauthorized package issue
        unauth_issues = [i for i in issues if "unauthorized package" in i["description"]]
        assert len(unauth_issues) == 1
        assert unauth_issues[0]["file"] == "internal/handlers"
        
        # Verify wrong module import prefix issue
        wrong_prefix_issues = [i for i in issues if "wrong module import prefix" in i["description"]]
        assert len(wrong_prefix_issues) == 1
        assert wrong_prefix_issues[0]["file"] == "internal/api/handler.go"

    def test_build_file_prompt_with_wiring_contract(self, tmp_path):
        from unittest.mock import MagicMock
        from pathlib import Path
        from llamaindex_crew.orchestrator.task_manager import TaskManager, TaskDefinition
        
        manager = MagicMock(spec=TaskManager)
        manager.build_file_prompt = TaskManager.build_file_prompt.__get__(manager, TaskManager)
        manager.db_path = Path(tmp_path) / "tasks.db"
        
        task = TaskDefinition(
            task_id="task-1",
            phase="dev",
            task_type="file",
            description="Write CreateSandbox handler",
            metadata={"file_path": "internal/api/handler.go"}
        )
        
        contract = {
            "version": 1,
            "module": "sandbox-api",
            "packages": {
                "internal/api": {
                    "files": ["handler.go"],
                    "owns": ["CreateSandbox"]
                }
            },
            "symbols": {
                "CreateSandbox": {"package": "internal/api"}
            },
            "deps": []
        }
        
        prompt = manager.build_file_prompt(
            task,
            project_vision="Create the best sandbox api",
            wiring_contract=contract
        )
        
        assert "WIRING CONTRACT REFERENCE" in prompt
        assert "CURRENT PACKAGE: internal/api" in prompt
        assert "OWNED CONCEPTS / SYMBOLS: CreateSandbox" in prompt
        
        assert "PROJECT IDENTITY:" in prompt
        assert "Module / import root: sandbox-api" in prompt
        assert "All local imports MUST use this exact prefix." in prompt

    def test_pass2_file_tree_with_wiring_contract(self):
        # We test the prompt generation/formatting function copy from tech_architect_agent
        def format_pass2_file_tree_prompt(ctx, stack_decisions, wiring_contract):
            solution_contract = (ctx.get("solution_contract") or "")[:4000]
            if wiring_contract:
                pkgs = wiring_contract.get("packages") or {}
                pkg_list = []
                for pkg, pkg_data in pkgs.items():
                    files = pkg_data.get("files") or []
                    pkg_list.append(f"- {pkg} (expected files: {', '.join(files)})")
                pkg_str = "\n".join(pkg_list)
                wiring_section = (
                    "APPROVED PACKAGES & MODULE CONTRACTS (MANDATORY):\n"
                    "You MUST use these directory prefixes only:\n"
                    f"{pkg_str}\n\n"
                    "Do NOT introduce alternate packages for the same ownership (e.g. do NOT add `internal/handlers` if `internal/api` is approved).\n"
                    "All code generated must reside within these packages and files.\n"
                )
                solution_contract = f"{wiring_section}\n{solution_contract}"

            _PASS2_FILE_TREE_PROMPT = "{solution_contract}\n{component_section}\n{stack_decisions}\n{design_spec}\n{skill_context}"
            prompt = _PASS2_FILE_TREE_PROMPT.format(
                stack_decisions=stack_decisions,
                design_spec=ctx["design_spec"][:6000],
                skill_context=ctx["skill_context"][:3000],
                solution_contract=solution_contract,
                component_section="components list",
            )
            return prompt

        ctx = {
            "design_spec": "Design Spec content",
            "skill_context": "Skill Context content",
            "solution_contract": "Solution Contract content"
        }
        
        contract = {
            "version": 1,
            "module": "sandbox-api",
            "packages": {
                "internal/api": {
                    "files": ["handler.go"],
                    "owns": ["CreateSandbox"]
                }
            }
        }
        
        sent_prompt = format_pass2_file_tree_prompt(ctx, "stack decisions", wiring_contract=contract)
        
        assert "APPROVED PACKAGES & MODULE CONTRACTS (MANDATORY):" in sent_prompt
        assert "You MUST use these directory prefixes only:" in sent_prompt
        assert "- internal/api (expected files: handler.go)" in sent_prompt
        assert "Do NOT introduce alternate packages for the same ownership" in sent_prompt

    def test_parse_emitted_wiring_contract(self):
        text = """
Some prose
<wiring_contract>
{
  "version": 1,
  "module": "sandbox-api",
  "packages": {
    "internal/api": {"files": ["internal/api/handler.go"], "owns": ["CreateSandbox"]}
  },
  "symbols": {
    "CreateSandbox": {"package": "internal/api", "signature": "func CreateSandbox() error"}
  },
  "deps": []
}
</wiring_contract>
"""
        parsed = parse_emitted_wiring_contract(text)
        assert parsed is not None
        assert parsed["_meta"]["source"] == "emitted"
        assert parsed["_meta"]["enforcement"] == "strict"
        assert is_strict_wiring_enforcement(parsed)

    def test_parse_emitted_invalid_json_is_non_fatal(self):
        assert parse_emitted_wiring_contract("<wiring_contract>{not json}</wiring_contract>") is None

    def test_parse_emitted_wiring_patch(self):
        text = """
Paths: internal/api/handler.go
<wiring_patch>
.module = "sandbox-api"
| .packages["internal/api"].owns = ["CreateSandbox"]
</wiring_patch>
"""
        patch = parse_emitted_wiring_patch(text)
        assert patch is not None
        assert '.module = "sandbox-api"' in patch
        assert "CreateSandbox" in patch

    def test_parse_emitted_wiring_patch_prefers_design_over_solution(self):
        solution = '<wiring_patch>.module = "from-solution"</wiring_patch>'
        design = '<wiring_patch>.module = "from-design"</wiring_patch>'
        assert parse_emitted_wiring_patch(design, solution) == '.module = "from-design"'
        assert parse_emitted_wiring_patch(solution, design) == '.module = "from-solution"'

    @pytest.mark.skipif(not shutil.which("jq"), reason="jq not installed")
    def test_apply_wiring_patch_sets_module_owns_and_deps(self, path_seed_contract):
        patch = """
.module = "sandbox-api"
| .language = "go"
| .packages["internal/api"].owns = ["CreateSandbox", "ExecuteCommand"]
| .deps += [{"from": "internal/api", "to": "internal/podman"}]
"""
        result = apply_wiring_patch(path_seed_contract, patch)
        assert result is not None
        assert result["module"] == "sandbox-api"
        assert result["language"] == "go"
        assert result["packages"]["internal/api"]["owns"] == ["CreateSandbox", "ExecuteCommand"]
        assert {"from": "internal/api", "to": "internal/podman"} in result["deps"]
        assert result["_meta"]["source"] == "jq-patch"
        assert result["_meta"]["enforcement"] == "strict"

    @pytest.mark.skipif(not shutil.which("jq"), reason="jq not installed")
    def test_apply_wiring_patch_strips_code_fences(self, path_seed_contract):
        patch = """```jq
.module = "fenced-module"
```"""
        result = apply_wiring_patch(path_seed_contract, patch)
        assert result is not None
        assert result["module"] == "fenced-module"

    @pytest.mark.skipif(not shutil.which("jq"), reason="jq not installed")
    def test_apply_wiring_patch_invalid_jq_returns_none(self, path_seed_contract):
        assert apply_wiring_patch(path_seed_contract, ".module = broken syntax {{{") is None

    def test_apply_wiring_patch_forbidden_construct_returns_none(self, path_seed_contract):
        with patch("llamaindex_crew.utils.wiring_contract.resolve_jq_bin", return_value="/usr/bin/jq"):
            assert apply_wiring_patch(path_seed_contract, "input_filename") is None
            assert apply_wiring_patch(path_seed_contract, "def foo: .; foo") is None

    def test_apply_wiring_patch_without_jq_returns_none(self, path_seed_contract):
        with patch("llamaindex_crew.utils.wiring_contract.resolve_jq_bin", return_value=None):
            assert apply_wiring_patch(path_seed_contract, '.module = "x"') is None

    @pytest.mark.skipif(not shutil.which("jq"), reason="jq not installed")
    def test_extract_wiring_contract_applies_jq_patch_on_seed(self):
        solution_spec = """
module github.com/example/sandbox-api
- internal/api/handler.go
- internal/podman/podman.go
"""
        design_spec = """
<wiring_patch>
.module = "sandbox-api"
| .packages["internal/api"].owns = ["CreateSandbox"]
| .deps += [{"from": "internal/api", "to": "internal/podman"}]
</wiring_patch>
"""
        contract = extract_wiring_contract_from_specs(solution_spec, design_spec)
        assert contract["module"] == "sandbox-api"
        assert contract["packages"]["internal/api"]["owns"] == ["CreateSandbox"]
        assert contract["_meta"]["source"] == "jq-patch"

    @pytest.mark.skipif(not shutil.which("jq"), reason="jq not installed")
    def test_extract_full_json_emit_wins_over_jq_patch(self):
        solution_spec = """
<wiring_contract>
{
  "version": 1,
  "module": "json-module",
  "packages": {
    "internal/api": {"files": ["internal/api/handler.go"], "owns": ["FromJson"]}
  },
  "symbols": {},
  "deps": []
}
</wiring_contract>
"""
        design_spec = '<wiring_patch>.module = "patch-module"</wiring_patch>'
        contract = extract_wiring_contract_from_specs(solution_spec, design_spec)
        assert contract["module"] == "json-module"
        assert contract["_meta"]["source"] == "emitted"

    @pytest.mark.skipif(not shutil.which("jq"), reason="jq not installed")
    def test_extract_invalid_patch_falls_back_to_path_seed(self):
        solution_spec = "- internal/api/handler.go"
        design_spec = "<wiring_patch>.this is not valid jq {{{</wiring_patch>"
        contract = extract_wiring_contract_from_specs(solution_spec, design_spec)
        assert contract["_meta"]["source"] == "extract-fallback"
        assert "internal/api" in contract["packages"]

    def test_build_path_only_contract_from_specs(self):
        contract = _build_path_only_contract_from_specs(
            "module github.com/example/my-app\n- internal/api/handler.go",
            "",
        )
        assert contract["module"] == "github.com/example/my-app"
        assert contract["_meta"]["source"] == "extract-fallback"

    def test_unicode_tree_tech_stack_builds_packages(self):
        tech_stack = """
## File Structure
```
sandbox-api/
├── internal/
│   ├── api/
│   │   └── handler.go
│   └── sandbox/
│       └── sandbox.go
├── cmd/
│   └── main.go
└── go.mod
```
"""
        contract = extract_wiring_contract_from_specs(
            "",
            "",
            language_hint="go",
            tech_stack=tech_stack,
        )
        assert "internal/api" in contract["packages"]
        assert "internal/sandbox" in contract["packages"]
        assert "internal/api/handler.go" in contract["packages"]["internal/api"]["files"]
        assert "internal/sandbox/sandbox.go" in contract["packages"]["internal/sandbox"]["files"]
        assert contract["_meta"]["source"] == "extract-fallback"

    def test_unicode_tree_in_design_spec_builds_packages(self):
        design_spec = """
# Design
```
my-app/
├── src/
│   ├── api/
│   │   └── routes.py
│   └── services/
│       └── orders.py
└── README.md
```
"""
        contract = extract_wiring_contract_from_specs("", design_spec, language_hint="python")
        assert "src/api" in contract["packages"]
        assert "src/services" in contract["packages"]
        assert "src/api/routes.py" in contract["packages"]["src/api"]["files"]


class TestPlannedEmitEngine:
    def test_symbol_key_qualified(self):
        assert symbol_key("internal/api", "Create") == "internal/api.Create"
        assert symbol_key("internal/api", "internal/api.Create") == "internal/api.Create"

    def test_weak_module_names(self):
        assert _is_weak_module_name("cmd")
        assert _is_weak_module_name("src")
        assert _is_weak_module_name("api")
        assert _is_weak_module_name("sandbox")
        assert _is_weak_module_name("service")
        assert not _is_weak_module_name("sandbox-api")
        assert not _is_weak_module_name("github.com/acme/sandbox-api")
        assert not _is_weak_module_name("@acme/billing")

    def test_sync_module_from_go_mod_overrides_weak(self, tmp_path):
        from llamaindex_crew.utils.wiring_contract import sync_module_identity_from_workspace
        (tmp_path / "go.mod").write_text("module github.com/example/sandbox-api\n\ngo 1.22\n")
        contract = {
            "version": 1,
            "module": "api",
            "language": "unknown",
            "packages": {},
            "symbols": {},
            "deps": [],
        }
        out = sync_module_identity_from_workspace(contract, tmp_path)
        assert out["module"] == "github.com/example/sandbox-api"
        assert out["language"] == "go"

    def test_sync_module_from_package_json(self, tmp_path):
        from llamaindex_crew.utils.wiring_contract import sync_module_identity_from_workspace
        (tmp_path / "package.json").write_text('{"name": "@acme/billing-ui"}', encoding="utf-8")
        contract = {"version": 1, "module": "api", "language": "unknown", "packages": {}, "deps": []}
        out = sync_module_identity_from_workspace(contract, tmp_path)
        assert out["module"] == "@acme/billing-ui"
        assert out["language"] in ("javascript", "typescript")

    def test_sync_module_from_pyproject(self, tmp_path):
        from llamaindex_crew.utils.wiring_contract import sync_module_identity_from_workspace
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "order_service"\nversion = "0.1.0"\n',
            encoding="utf-8",
        )
        contract = {"version": 1, "module": "src", "language": "unknown", "packages": {}, "deps": []}
        out = sync_module_identity_from_workspace(contract, tmp_path)
        assert out["module"] == "order_service"
        assert out["language"] == "python"

    def test_sync_module_from_cargo(self, tmp_path):
        from llamaindex_crew.utils.wiring_contract import sync_module_identity_from_workspace
        (tmp_path / "Cargo.toml").write_text(
            '[package]\nname = "sandbox_runtime"\nversion = "0.1.0"\nedition = "2021"\n',
            encoding="utf-8",
        )
        contract = {"version": 1, "module": "lib", "language": "unknown", "packages": {}, "deps": []}
        out = sync_module_identity_from_workspace(contract, tmp_path)
        assert out["module"] == "sandbox_runtime"
        assert out["language"] == "rust"

    def test_sync_module_from_build_sbt(self, tmp_path):
        from llamaindex_crew.utils.wiring_contract import sync_module_identity_from_workspace
        (tmp_path / "build.sbt").write_text(
            'ThisBuild / organization := "com.acme"\nname := "billing"\nscalaVersion := "2.13.12"\n',
            encoding="utf-8",
        )
        contract = {"version": 1, "module": "api", "language": "unknown", "packages": {}, "deps": []}
        out = sync_module_identity_from_workspace(contract, tmp_path)
        assert out["module"] == "com.acme"
        assert out["language"] == "scala"

    def test_html_only_sets_language_without_module(self, tmp_path):
        from llamaindex_crew.utils.wiring_contract import read_package_manifest_identity
        (tmp_path / "index.html").write_text("<html><body>hi</body></html>", encoding="utf-8")
        mod, lang = read_package_manifest_identity(tmp_path)
        assert mod is None
        assert lang == "html"

    def test_patch_allows_python_def_inside_signature_strings(self):
        from llamaindex_crew.utils.wiring_contract import (
            _normalize_jq_deps_assignments,
            _patch_program_is_safe,
        )
        program = (
            '.module = "calculator"\n'
            '| .language = "python"\n'
            '| .symbols["calculator.Calculator"] = '
            '{"package":"calculator","signature":"class Calculator:\\n    def add(self, a, b): ..."}\n'
            '| .deps["cli"] = ["calculator"]'
        )
        assert _patch_program_is_safe(program)
        normalized = _normalize_jq_deps_assignments(program)
        assert '.deps += [{"from":"cli","to":"calculator"}]' in normalized
        assert '.deps["cli"]' not in normalized

    def test_patch_rejects_real_jq_def(self):
        from llamaindex_crew.utils.wiring_contract import _patch_program_is_safe
        assert not _patch_program_is_safe('def foo: .;\n.module = "x"')

    def test_normalize_symbol_keys_migrates_bare(self):
        contract = {
            "version": 1,
            "module": "app",
            "packages": {"internal/api": {"files": [], "owns": ["Create"]}},
            "symbols": {
                "Create": {"package": "internal/api", "signature": "func Create()"},
            },
            "deps": [],
        }
        out = normalize_symbol_keys(contract)
        assert "internal/api.Create" in out["symbols"]
        assert "Create" not in out["symbols"]

    def test_extract_planned_interfaces_go_and_python(self):
        text = """
## internal/sandbox
func (m *Manager) CreateSandbox(ctx context.Context, image string) (string, error)

## src/services
def create_order(user_id: str) -> Order:
"""
        packages = {
            "internal/sandbox": {"files": [], "owns": []},
            "src/services": {"files": [], "owns": []},
        }
        planned = extract_planned_interfaces_from_specs(text, packages=packages)
        assert "internal/sandbox.CreateSandbox" in planned["symbols"]
        assert "CreateSandbox" in planned["owns_by_package"]["internal/sandbox"]
        assert "src/services.create_order" in planned["symbols"] or "create_order" in (
            planned["owns_by_package"].get("src/services") or []
        )

    def test_strengthen_rejects_weak_module_prefers_title(self):
        seed = {
            "version": 1,
            "module": "cmd",
            "language": "go",
            "packages": {
                "internal/api": {"files": ["internal/api/handler.go"], "owns": []},
            },
            "symbols": {},
            "deps": [],
            "_meta": {"source": "extract-fallback", "enforcement": "relaxed"},
        }
        design = """
# Sandbox API
## internal/api
func CreateSandboxHandler(mgr *Manager) http.HandlerFunc
"""
        out = strengthen_contract_from_specs(seed, design)
        assert out["module"] == "sandbox-api"
        assert contract_has_planned_apis(out)
        assert "internal/api.CreateSandboxHandler" in out["symbols"]
        assert out["_meta"]["source"] == "spec-interfaces"

    def test_contract_has_planned_apis_false_for_empty_owns(self):
        assert not contract_has_planned_apis({
            "packages": {"internal/api": {"files": ["a.go"], "owns": []}},
            "symbols": {},
        })

    def test_sig_line_rejects_prose_name_paren(self):
        """Bare 'clients (AI agents)' must not become a planned symbol."""
        text = """
## cmd
Supports multiple clients (AI agents, CLI tools, and dashboards).
"""
        packages = {"cmd": {"files": ["cmd/main.go"], "owns": []}}
        planned = extract_planned_interfaces_from_specs(text, packages=packages)
        assert "clients" not in (planned.get("owns_by_package") or {}).get("cmd", [])
        assert not any(k.endswith(".clients") or k == "clients" for k in (planned.get("symbols") or {}))

    def test_sig_line_still_matches_keyworded_methods(self):
        text = """
## internal/api
public async CreateSandbox(ctx: Context): Promise<string>
public void HandleRequest(HttpServletRequest req)
"""
        packages = {"internal/api": {"files": ["internal/api/handler.ts"], "owns": []}}
        planned = extract_planned_interfaces_from_specs(text, packages=packages)
        owns = planned["owns_by_package"]["internal/api"]
        assert "CreateSandbox" in owns
        assert "HandleRequest" in owns

    def test_path_seed_rejects_symbol_like_extension(self):
        """pkg.Symbol must not become a file path (e.g. internal/api.CreateHandler)."""
        text = """
- internal/api/handler.go
- internal/api.CreateHandler
- pkg/sandbox.Manager
"""
        seed = _build_path_only_contract_from_specs(text, "")
        files = all_declared_files(seed)
        assert "internal/api/handler.go" in files
        assert "internal/api.CreateHandler" not in files
        assert "pkg/sandbox.Manager" not in files
        assert "internal/api.CreateHandler" not in seed["packages"]

    def test_strengthen_skips_prose_over_jq_patch(self):
        patched = {
            "version": 1,
            "module": "sandbox-api",
            "language": "go",
            "packages": {
                "internal/api": {
                    "files": ["internal/api/handler.go"],
                    "owns": ["CreateSandbox"],
                },
            },
            "symbols": {
                "internal/api.CreateSandbox": {
                    "package": "internal/api",
                    "signature": "func CreateSandbox(ctx context.Context) error",
                    "exports": ["CreateSandbox"],
                },
            },
            "deps": [],
            "_meta": {"source": "jq-patch", "enforcement": "strict"},
        }
        prose = """
## cmd
Supports multiple clients (AI agents).
## internal/api
func FakeFromProse() error
"""
        out = strengthen_contract_from_specs(patched, prose)
        assert out["_meta"]["source"] == "jq-patch"
        assert "FakeFromProse" not in out["packages"]["internal/api"]["owns"]
        assert "internal/api.FakeFromProse" not in out["symbols"]
        assert "clients" not in out["packages"].get("cmd", {}).get("owns", [])

    def test_tech_stack_rejects_competing_sandbox_vs_service(self):
        contract = {
            "version": 1,
            "module": "my-sandbox-api",
            "language": "go",
            "packages": {
                "internal/api": {"files": ["internal/api/handler.go"], "owns": ["CreateHandler"]},
                "internal/service": {
                    "files": ["internal/service/manager.go"],
                    "owns": ["NewService", "Create"],
                },
            },
            "symbols": {},
            "deps": [],
            "_meta": {"source": "jq-patch", "enforcement": "strict"},
        }
        paths = [
            "internal/api/handler.go",
            "internal/service/manager.go",
            "internal/sandbox/manager.go",
            "internal/sandbox/podman.go",
        ]
        msg = tech_stack_violates_contract(contract, "tree", paths)
        assert msg is not None
        assert "internal/sandbox" in msg
        assert "internal/api" in msg or "internal/service" in msg

    def test_tech_stack_allows_util_alongside_service(self):
        contract = {
            "version": 1,
            "module": "my-sandbox-api",
            "packages": {
                "internal/api": {"files": ["internal/api/handler.go"], "owns": ["CreateHandler"]},
                "internal/service": {"files": ["internal/service/manager.go"], "owns": ["Create"]},
                "internal/util": {"files": ["internal/util/limiter.go"], "owns": []},
            },
            "symbols": {},
            "deps": [],
            "_meta": {"source": "jq-patch", "enforcement": "relaxed"},
        }
        paths = [
            "internal/api/handler.go",
            "internal/service/manager.go",
            "internal/util/limiter.go",
        ]
        assert tech_stack_violates_contract(contract, "tree", paths) is None

    def test_build_creation_manifest_includes_c_sources(self):
        from llamaindex_crew.utils.wiring_contract import (
            build_creation_manifest,
        )
        contract = {
            "version": 1,
            "module": "embedded",
            "packages": {
                "src/service": {
                    "files": ["src/service/manager.c", "include/service/manager.h"],
                    "owns": [],
                },
            },
            "symbols": {},
            "deps": [],
        }
        manifest = build_creation_manifest(contract, [], "")
        paths = {e["path"] for e in manifest}
        assert "src/service/manager.c" in paths
        assert "include/service/manager.h" in paths

    def test_validate_supplementary_competing_package(self, sample_contract_data):
        supp = [
            {"path": "internal/sandbox/manager.go", "description": "bad"},
        ]
        from llamaindex_crew.utils.wiring_contract import validate_supplementary_paths, tech_stack_violates_contract
        validated = validate_supplementary_paths(supp, sample_contract_data)
        paths = [e["path"] for e in validated]
        msg = tech_stack_violates_contract(sample_contract_data, "", paths)
        assert msg is not None

    def test_missing_declared_source_files(self, tmp_path):
        from llamaindex_crew.utils.wiring_contract import missing_declared_source_files
        contract = {
            "version": 1,
            "module": "my-sandbox-api",
            "packages": {
                "internal/service": {
                    "files": [
                        "internal/service/manager.go",
                        "internal/service/README.md",
                    ],
                    "owns": ["Create"],
                },
            },
            "symbols": {},
            "deps": [],
        }
        (tmp_path / "internal" / "service").mkdir(parents=True)
        assert missing_declared_source_files(contract, tmp_path) == [
            "internal/service/manager.go"
        ]
        (tmp_path / "internal" / "service" / "manager.go").write_text("package service\n")
        assert missing_declared_source_files(contract, tmp_path) == []

    def test_reconcile_flags_missing_declared_files(self, tmp_path):
        contract = {
            "version": 1,
            "module": "my-sandbox-api",
            "packages": {
                "internal/service": {
                    "files": ["internal/service/manager.go"],
                    "owns": ["Create"],
                },
            },
            "symbols": {},
            "deps": [],
            "_meta": {"source": "jq-patch", "enforcement": "strict"},
        }
        (tmp_path / "internal" / "service").mkdir(parents=True)
        issues = reconcile_workspace_against_contract(contract, tmp_path)
        assert any("declared source file missing" in i["description"] for i in issues)

    def test_package_has_domain_keywords_sandbox(self):
        assert package_has_domain_keywords("internal/sandbox")
        assert package_has_domain_keywords("internal/service")
        assert not package_has_domain_keywords("internal/util")
