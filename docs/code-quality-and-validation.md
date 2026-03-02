# Code Quality and Validation

The platform validates generated code during the development phase to catch incomplete or broken output and to support multiple target languages (Python, Java, JavaScript/TypeScript, etc.).

## Goals

- Detect stubs, placeholders, TODOs, and truncated output.
- Check syntax and imports so that generated code is syntactically valid and properly integrated.
- Support **multi-language** validation (not only Python) so jobs can target Java, Quarkus, JS/TS, etc.
- Retry file generation when validation fails, with feedback to the LLM.

## Components

| Component | Location | Role |
|-----------|----------|------|
| **CodeCompletenessValidator** | `agent/src/llamaindex_crew/orchestrator/code_validator.py` | Completeness checks, syntax, and import validation per language. |
| **TaskManager** | `agent/src/llamaindex_crew/orchestrator/task_manager.py` | Builds file-creation prompts with project vision, dependency content, and cross-file consistency instructions. |
| **SoftwareDevWorkflow** | `agent/src/llamaindex_crew/workflows/software_dev_workflow.py` | Per-file generation loop with validation and retry; workspace-wide validation at end of development. |
| **Granular tasks** | SQLite DB (e.g. `tasks_<job_id>.db`) | File-level tasks with dependencies; same pattern as migration. |

## Completeness Checks

- Minimum non-comment line count and character count.
- Detection of stub patterns: `TODO` comments, `NotImplementedError`, console.log stubs, etc.
- Placeholder components (e.g. minimal React components with no real logic).
- `pass`-only method bodies (Python).

## Syntax Validation

- **Python:** `ast.parse()`; invalid syntax → validation failure.
- **Java / JS / TS / etc.:** Brace-balancing in code (ignoring strings and comments) to catch obvious structural errors.

## Import Validation

- **Python:** AST-based; local imports must resolve to existing files; stdlib and known third-party are allowed.
- **Java:** `import` parsing; stdlib and Maven/Gradle dependencies allowed; local packages must resolve to existing `.java` files.
- **JavaScript/TypeScript:** `import`/`require` parsing; npm packages from `package.json` allowed; relative paths must resolve to existing files.

## Per-File Validation and Retry

1. After generating a file, the workflow runs `CodeCompletenessValidator.validate_file_integration()` (syntax + imports) and optionally `validate_file()` (completeness).
2. If validation fails, the agent is given a **retry prompt** with the reported errors (e.g. syntax or import issues).
3. One retry is allowed per file (configurable); if it still fails, the task is marked accordingly and the workflow continues or fails as configured.

## Workspace-Wide Validation

At the end of the development phase, the workflow runs a workspace-wide check over relevant source files (e.g. `.py`, `.java`, `.js`, `.ts`, `.tsx`, `.go`, etc.) to surface integration issues across the whole project.

## Refactor and Migration

- **Refactor** (`crew_studio/refactor/runner.py`): Uses the same validator for post-refactor file and workspace checks.
- **Migration** (`crew_studio/migration/runner.py`): Uses the validator in addition to Java-specific structural validation.

## Configuration and LLM/Embeddings

- **LLM:** Configured via `~/.crew-ai/config.yaml` (e.g. Red Hat MaaS); used for all generation and refinement.
- **Embeddings:** Set globally to **HuggingFace** (e.g. `BAAI/bge-small-en-v1.5`) in `agent/src/llamaindex_crew/__init__.py` and in the workflow so that no OpenAI embedding calls are made (avoids quota issues when using only MaaS for LLM).

## Tests

- Unit tests for the validator and task manager: `agent/tests/unit/test_code_quality.py` (e.g. syntax/import/integration for Python, Java, JS/TS; enriched prompt content).
- Backend/API tests for pagination, filtering, and sorting: see repo `tests/` and `agent/tests/api/`.
