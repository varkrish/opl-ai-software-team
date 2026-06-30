# Changelog

All notable changes to the OPL AI Software Team backend are documented here.

## [Unreleased] — 2026-06-30

### Added

- **Solutioning loop** — config-gated research → architect → critique pass between Meta and Product Owner phases. Produces `solution_candidates.json`, `solution_spec.md`, and per-pass critique artifacts; pauses at `pending_solution_review` for human approval.
- **Solution agents** — `SolutionResearchAgent` (GitHub + skills), `SolutionArchitectAgent`, `SolutionCritiqueAgent` with prompts under `agent/src/ai_software_dev_crew/prompts/solutioning/`.
- **GitHub search tools** — `GitHubSearchReposTool` and `GitHubRepoReadmeTool` with rate limiting and graceful no-token degradation.
- **Solution API** — `GET /api/jobs/{id}/solution`, `POST /api/jobs/{id}/solution/refine`; extended `POST /api/jobs/{id}/approve` to distinguish solution vs plan review gates.
- **Workflow settings API** — `GET/POST/DELETE /api/workflow/config` for per-user plan review, solutioning, and auto-approve preferences (stored in SQLite, merged into job config at runtime).
- **User GitHub token in solutioning** — research agent uses the job owner's saved GitHub PAT when available.

### Changed

- **`SoftwareDevWorkflow`** — solutioning gate in normal and epic build paths; PO phase injects `solution_spec.md` into project context; resume checkpoint recognizes `solution_spec.md`.
- **`config.example.yaml`** — documented `plan_review` and `solutioning` sections.

### Tests

- Unit tests for GitHub tools, solutioning loop, workflow gate, and workflow config merge.
- API tests for solution and workflow config endpoints.
