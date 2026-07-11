# Changelog

All notable changes to **OPL Crew Backend** (`opl-ai-software-team`) are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).  
Version tags match container releases (`v2.x.y` → `quay.io/varkrish/crew-backend`).

## [Unreleased]

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

[Unreleased]: https://github.com/varkrish/opl-ai-software-team/compare/v2.2.0...HEAD
[2.2.0]: https://github.com/varkrish/opl-ai-software-team/compare/v2.1.0...v2.2.0
[2.1.0]: https://github.com/varkrish/opl-ai-software-team/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/varkrish/opl-ai-software-team/releases/tag/v2.0.0
