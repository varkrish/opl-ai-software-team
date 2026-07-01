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

### Fixed

- **`SolutionArchitectAgent.run()`** — revision passes were silently discarded: the fallback write to `solution_spec.md` only fired when the file did not already exist, so critique feedback on passes 2+ never reached disk and the same stale draft was re-reviewed every pass. Now compares file content before/after the agent call and overwrites whenever the agent didn't write via its own file tool, on every pass.
- **Solutioning loop artifacts** — each pass's `solution_spec.md` is now archived as `solution_spec_pass_N.md` (alongside the existing `solution_critique_pass_N.json`) so revisions can be diffed pass-over-pass instead of only keeping the final version.

### Known limitations

- **Reference repository recommendations are advisory only, not verified or acted on.** The research pass's `solution_candidates.json` (and the resulting "Recommended approach: fork X" text in `solution_spec.md`) is not cross-checked against real `github_search_repos` results, so candidate repos can be hallucinated (observed: a recommended repo that does not exist on GitHub). Nothing in the pipeline parses the candidate/spec to perform a deterministic clone — `GitTool` (which can `git clone`) is only available to the Developer agent, and whether it forks the recommended repo is left entirely to LLM judgment during the dev phase, with no verification, license gate, or brownfield wiring into Product Owner/Tech Architect. In an earlier iteration, giving agents (e.g. MetaAgent) unrestricted git-clone access caused hallucinated/oversized clones (e.g. cloning the main Frappe repo instead of scaffolding a custom app) — see the guard comment in `agent/src/llamaindex_crew/agents/meta_agent.py`. This "clone-and-edit-existing-code" path needs refinement (candidate verification, license allow-list, deterministic clone step reusing `crew_studio.llamaindex_web_app._clone_github_repo`) before it can be trusted; until then, expect the dev phase to build from scratch even when a strong reference match is found.

### Tests

- Unit tests for GitHub tools, solutioning loop, workflow gate, and workflow config merge.
- API tests for solution and workflow config endpoints.
- Regression tests for `SolutionArchitectAgent` spec persistence (`test_solution_architect_agent.py`) and per-pass spec archiving (`test_solutioning_loop.py`).
