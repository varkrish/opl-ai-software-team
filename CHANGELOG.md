# Changelog

All notable changes to **OPL Crew Backend** (`opl-ai-software-team`) are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).  
Version tags match container releases (`v2.x.y` → `quay.io/varkrish/crew-backend` / `ghcr.io/varkrish/crew-backend`).

## [Unreleased]

### Added
- **Runtime code execution via the Sandbox API** — new `sandbox_api` value for `SMOKE_TEST_BACKEND` runs generated code in an isolated container reached over HTTP (`SANDBOX_API_URL`), so validation no longer stops at static analysis. Needs no local container runtime or shared filesystem: the workspace is tarred and uploaded
- **`sandbox_execute` agent tool** — lets the dev and test agents run code, execute tests, or reproduce an error before fixing it. Registered only when `SANDBOX_API_URL` is set
- **Live preview** — `GET`/`POST`/`DELETE /api/jobs/<id>/live-preview` start and stop the generated app in a preview sandbox and return a URL. Start command comes from `preview_command:` in `test_plan.md`, else is detected (npm `start`/`dev`, a Python entrypoint or single `__main__` file, `go run`). Waits until the app actually serves and reports the app's own output when it does not, instead of returning a URL that answers nothing
- **`GET /api/llm/status`** — reports `{ configured, source: byok|server|none, hint? }` for UI/CLI preflight
- **LLM readiness gate on job create** — `POST /api/jobs` returns `422` with `code: llm_not_configured` when neither BYOK nor server fallback (`config.yaml` / `LLM_API_KEY`) is available, so jobs never sit forever in Queued

### Fixed
- **Build failures now reach the fix loop** — `smoke_test` was the only failing check that produced no fixable issue, so a job was marked `completed_with_errors` and the broken code was never handed back to the dev agent. Compiler/runtime output is now attributed to the files it names (Python tracebacks, Maven, and the generic `path:line:col` form), with frames outside the workspace discarded so the agent never edits stdlib or vendored code
- **Fix loop no longer stops while it is making progress** — it required the issue count to strictly decrease, but compilers cascade: fixing three errors lets the build advance and surface five more, and the loop declared "converged" at exactly the wrong moment. It now stops when the issue set stops *changing*, and allows more passes when code is actually executed
- **Failing checks are persisted to `validation_issues`** — that table was populated only from the external validator, so checks owned by the in-process suite left the UI with a failed build and no stated reason
- **Arbitrary file read via `/api/workspace/files/<path>`** — the route built a filesystem path straight from user input while the existing `_is_safe_relative_path` guard was never called, so `..` segments escaped the workspace. Now validated, with a resolved-path containment check so symlinks cannot escape either
- Flask multipart job create applies the same LLM credential check as the FastAPI path

## [2.5.0] - 2026-07-16

### Module integrity and code quality
- Wiring contract + creation manifests with language-neutral module identity from package manifests
- Harden import roots, stub/codegen integrity, and stack-lock coherence (ignore negated tech tokens)
- Multi-language fast E2E: Python, Java, Go, HTML, Node.js, Spring Boot

### Configurable workflows
- `workflow_resolver` for YAML / smart_router pipelines; plan-approve resumes on the resolved path
- TDD QA before build; feature-by-feature development when PO is in pipeline
- Consistent auto-approve gates; solutioning critique cannot approve with open `must_fix`

### Skill preferences
- Skills are authoritative for layout/wiring; neutral fallback only when none match
- Exclusive skill-family gating from locked `chosen_stack`
- Prefetch via `SKILLS_SERVICE_URL` + `skill_prefetch.json` debug artifact

### Fixed
- GitHub push reliability; LLM 429 backoff; plan-approve / QA resume checkpoints
co
## [2.4.5] - 2026-07-13

### Fixed
- **Manifest derivation from approved solution_spec** — `write_stack_manifest_from_solution_spec` now scans the approved spec for data/cache/persistence signals (Redis, Upstash, PostgreSQL, etc.) and unlocks the `database` forbidden tier when the spec explicitly selects one. Previously the manifest was derived only from the short vision text, so specs with Redis caching still forbade `database`, causing false Tech Architect failures.

## [2.4.4] - 2026-07-13

### Changed
- **Agent prompts** — structured output contracts, dead prompt files removed, backstory deduplication and review/validation verdict signals.
- **Dev infra** — `--no-access-log` on uvicorn/validator; pytest default timeout raised to 900s.

### Added
- **`test_prompt_improvements.py`** — unit tests locking prompt contracts after the enhancement pass.

## [2.4.3] - 2026-07-13

### Fixed
- **Forbidden-tier false positives** — negated mentions (`without database tier`) and substring noise (`orm` inside `formatting`) no longer fail Tech Architect stack validation.

## [2.4.2] - 2026-07-13

### Fixed
- **Named-component extraction** — filesystem layout contracts (`/pages`, `src/api`, …) are excluded from named-component coverage; only real module names are checked. Folder layout remains enforced by concrete file-tree depth, not path hardcoding.

## [2.4.1] - 2026-07-13

### Fixed
- **Stack manifest vs chosen_stack conflict** — technology-agnostic tier unlock: if `chosen_stack` already selects a tier, drop it from effective `forbidden_tiers` and do not re-apply vision overreach against the locked contract.

## [2.4.0] - 2026-07-13

### Added
- **Pipeline-based workflow routing** — `_get_active_phases()` selects `fast` / `adaptive` / `full` phase lists; fast skips PO/Designer/Tech Architect and uses `seed_minimal_artifacts` before parallel development.
- **`capability_profile` API** — accepts string shorthand (`"fast"`|`"full"`|`"adaptive"`) or dict; Auto/default maps to adaptive inference.
- **Native FastAPI `/api/jobs/{id}/validation`** — authenticated validation report endpoint (no Flask header injection).

### Fixed
- Fast/Full job creation **422** — Pydantic model rejected plain-string `capability_profile`.
- Auto-detect never inferred — empty/unspecified path defaulted to `full` instead of vision-based adaptive.
- Fast mode produced **zero files** — task registration lived only in Tech Architect; seed phase now registers granular tasks and requires a parseable unicode file tree (with one strict retry).

## [2.3.0] - 2026-07-12

### Added
- **Dynamic Workflow Routing** — Introduced `fast`, `adaptive`, and `full` workflow profiles. The `fast` lane bypasses the heavy multi-agent solution loop via `_run_fast_stack_decision()`, while the `full` lane retains the deep iterative review process with user approval.

### Changed
- Enhanced QA Agent test plan generation (`test_plan_task.txt`) to produce a comprehensive Markdown document with explicit `Test Strategy` and `Test Data Strategy` sections while preserving the execution configuration block for the test runner.

### Fixed
- Replaced `Dockerfile` requirements with `Containerfile` in Tech Architect prompts, resolving an issue where downstream DevOps agents would generate both files, enforcing compliance with Red Hat stack standards.
- Silenced noisy Uvicorn HTTP access logs (`--no-access-log`) for the `backend`, `validator`, and `skills-service` containers in both development (`dev-backend.sh`, `compose.dev.yaml`) and production (`Containerfile.backend`, `compose.yaml`) environments.
- **`_TEST_FILE_TIER` ordering bug** — test files were assigned tier 15 (below source-file default of 50), causing `earlier_tasks` to be empty when dependency inference ran; raised to tier 95 so test files are always registered after all source files, restoring correct test-to-source dependency wiring.
- **`append_tldr_tools` mock signature** — test mock lambda lacked `**kwargs`, causing `TypeError` when the new `config=` keyword argument was added to the call site.
- **`create_workspace_file_tools` tool count** — test asserted 5 tools; function now returns 6 after `bulk_file_writer` and `replace_file_content` were added; assertion and tool-name checks updated.
- **`TestBuildRunnerThreadLocal` import path** — `crew_studio` sits above the `agent/` test root; added autouse fixture to inject the parent directory into `sys.path` and suppress `ensure_llm_api_key` in unit-test context (no real API key required).
- **`test_skips_if_features_already_exist`** — stub feature content (`"Feature: Existing\n"`, 18 chars) failed `is_valid_gherkin_feature`'s `min_chars=40` + Scenario-structure check, causing the file to be deleted before the assertion; replaced with a complete valid Gherkin feature.

## [2.2.0] - 2026-07-12

### Added

- **Tech Architect 3-pass pipeline** — stack selection, file-level tree, and implementation plan are separate LLM calls with `reset_chat()` between passes. Reduces MaaS timeouts and improves artifact quality.
- **File-tree depth validation** — `validate_tech_stack_completeness()` rejects shallow directory-only trees; minimum implementation file count is derived from named components in `solution_spec` / `design_spec` (no hardcoded framework rules).
- **Pass 2 retry loop** — Tech Architect retries the file-tree pass up to 3 times with structural validation feedback before writing `tech_stack.md`.
- **`vision_stack_analysis` module** — technology-agnostic stack briefs, approved-solution contract formatting, and component reflection checks for architecture drift detection.
- **Approved solution contract** — when the user approves `solution_spec.md`, downstream agents (Designer, Tech Architect, workflow validation) treat it as binding; vision overreach checks are skipped.
- **Plain-path fallback parser** in `TaskManager._extract_files_with_descriptions` for tech stacks that list paths without Unicode tree characters.
- **`crew_studio/test_tech_architect.py`** — isolated Tech Architect runner for prod smoke tests; uses `user_llm_context()` so Settings → LLM credentials match live jobs.
- **Task-level transient retry** for 503 / network errors during the development phase.
- Unit tests: `test_approved_solution_contract.py`, `test_vision_stack_analysis.py`, `test_adaptive_stack_routing.py`, `test_stack_manifest.py`, shallow-tree validation cases in `test_code_quality.py`.

### Fixed

- **Solutioning pass 2 crash** — reset architect/critique chat history between passes (fixes Vertex/LiteLLM “tool role without previous assistant” 400).
- **Architecture drift** — Tech Architect no longer ignores approved `solution_spec.md`; structural mismatch detection and stack brief enforce the reviewed architecture.
- **Empty file-creation task list** — high-level directory trees no longer pass validation; orchestrator now requires concrete source filenames with extensions so `file_creation` tasks are registered per file.
- **BYOK / Settings LLM** — isolated scripts and jobs resolve credentials from `user_llm_configs` (Settings → LLM) via `user_llm_context()`; stale `config.yaml` fallback no longer used when BYOK is configured.
- **Solution agent chat reuse** — `reset_chat()` before each solutioning architect/critique pass.
- **Python package `__init__.py` blocked by manifest guard** — strict dev-phase allowlist now includes companion `__init__.py` paths for registered subpackage modules; vendor stubs (e.g. `mlflow/__init__.py`) remain rejected.

### Changed

- `define_tech_stack_task.txt` and Tech Architect pass 2 prompt require Unicode tree format with **file-level** entries (no folder-only lines).
- Workflow passes `design_spec` and `solution_spec` into tech-stack completeness validation.
- `detect_solution_spec_mismatch()` uses named-component extraction and path/slug reflection instead of brittle keyword rules.

## [2.1.0] - 2026-07-10

### Fixed

- Fail jobs early when LLM API key is missing or undecryptable BYOK key would overwrite server fallback with an empty Bearer token.

## [2.0.0]

Earlier releases: solutioning loop, plan review, BYOK LLM config, workflow prefs API, refinement flows. See git tags for details.

[Unreleased]: https://github.com/varkrish/opl-ai-software-team/compare/v2.4.2...HEAD
[2.4.2]: https://github.com/varkrish/opl-ai-software-team/compare/v2.4.1...v2.4.2
[2.4.1]: https://github.com/varkrish/opl-ai-software-team/compare/v2.4.0...v2.4.1
[2.4.0]: https://github.com/varkrish/opl-ai-software-team/compare/v2.3.0...v2.4.0
[2.3.0]: https://github.com/varkrish/opl-ai-software-team/compare/v2.2.0...v2.3.0
[2.2.0]: https://github.com/varkrish/opl-ai-software-team/compare/v2.1.0...v2.2.0
[2.1.0]: https://github.com/varkrish/opl-ai-software-team/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/varkrish/opl-ai-software-team/releases/tag/v2.0.0
