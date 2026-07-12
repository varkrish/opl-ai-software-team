# Platform Overview

AI Software Development Crew (Crew Studio) is a multi-agent platform that turns a natural-language vision into production-ready code. This document describes the main functionalities as of the latest release.

## Landing / Create job

![Landing page — create a new job with vision and backend](images/landing.png)

## High-Level Capabilities

| Area | Description |
|------|-------------|
| **Build from vision** | Submit a short description (e.g. "create a simple calculator in JS"); the system runs Meta → Product Owner → Designer → Tech Architect → Development → Frontend phases and produces a full project. |
| **Task-level tracking** | Each phase is broken into granular tasks stored in SQLite. The dashboard and APIs expose progress, phase, and per-task status. |
| **Refinement** | After a job completes, use natural language to refine the generated code (file-level or project-wide) via the Refine panel. |
| **MTA migration** | Upload an MTA report and source code; the platform runs a two-phase pipeline to analyze and apply migration changes with per-file issue tracking. |
| **Refactor** | Run refactoring jobs (e.g. target stack change) with the same task-tracking and workspace model as builds. |
| **LLM & embeddings** | LLM calls use Red Hat MaaS (or any OpenAI-compatible API). Embeddings use local HuggingFace models by default (no OpenAI dependency). |

## Architecture at a Glance

- **Backend:** FastAPI (`crew_studio/asgi_app.py`) on port 8080; job and task data in SQLite (`crew_jobs.db`).
- **Frontend:** React + PatternFly + Vite ([opl-studio-ui](https://github.com/varkrish/opl-studio-ui) repo), dev server on port 3000; proxies `/api` and `/health` to the backend.
- **Agent framework:** LlamaIndex-based workflows and agents under `agent/src/llamaindex_crew/` (and legacy `ai_software_dev_crew`); granular tasks and code validation in `orchestrator/`.

## Workflow Profiles

Jobs are automatically routed into one of three capability profiles based on vision complexity, or you can override via `capability_profile` in `job.json`:

| Profile | Description | When used |
|---------|-------------|-----------|
| `fast` | Skips the solutioning loop; makes a direct stack decision via `_run_fast_stack_decision()`. Minimal overhead, ideal for simple scripts or POCs. | `client_deliverable` / `minimal` visions |
| `adaptive` | Lightweight architecture pass without the full Research → Critique cycle. Balances speed and structure. | Moderate complexity projects |
| `full` | Complete Research → Architect → Critique solutioning loop with optional human review gate at `pending_solution_review`. Maximum quality for complex multi-service apps. | Enterprise / multi-service visions |

## Workflow Phases

1. **Meta** — High-level plan and scope.
2. **Solutioning** *(full/adaptive)* — Research, Architect, and Critique agents iteratively refine the solution spec; human review gate at `pending_solution_review`.
3. **Product Owner** — User stories and requirements.
4. **Designer** — Design spec and feature breakdown.
5. **Tech Architect** — Tech stack and file-level task list (3-pass pipeline: stack selection → file tree → implementation plan).
6. **Development** — Per-file code generation with validation and retry; parallel file workers.
7. **Frontend** — UI/assets if applicable.
8. **Completed** — Job marked complete; outputs available in workspace and via Files UI.

## Key Documentation

- [Dashboard and UI](dashboard-and-ui.md) — Pagination, filtering, sorting, job search.
- [Code quality and validation](code-quality-and-validation.md) — Multi-language validation, retries, workspace checks.
- [Refinement & Studio UI](REFINEMENT_AND_UI.md) — Refine flow and UI behavior.
- [MTA migration](migration.md) — Upload, pipeline, and APIs.
