# Refinement & Studio UI

This document summarizes the prompt-based refinement flow and related Studio UI behavior.

## Refinement (Prompt-Based Edits)

After a job completes (or fails), you can refine the generated code using natural language.

- **Where:** Files page → floating **Refine** button → slide-out panel (right side).
- **Scope:** File-level (choose a file in the dropdown) or project-wide (no file selected).
- **Flow:** Enter a prompt (e.g. “add comments”, “delete unused file”), optional file scope, then Send. The job is marked **running** in the dashboard and progress is shown until the refinement completes or fails.

### Backend

- **Endpoints:** `POST /api/jobs/<id>/refine` (start), `GET /api/jobs/<id>/progress` (poll), `GET /api/jobs/<id>/refinements` (history).
- **Runner** (`crew_studio/refinement_runner.py`): Git snapshot, context load, then per-file or project-wide RefinementAgent runs. Detects file **writes**, **modifications**, and **deletes** (via `file_deleter` tool).
- **Agent** (`agent/src/llamaindex_crew/agents/refinement_agent.py`): Uses workspace-bound tools: `file_reader`, `file_writer`, `file_lister`, `file_deleter`. For explicit “delete” prompts, the agent is instructed to call `file_deleter` so the file is removed from the filesystem (not just emptied).

### Dashboard behavior

- When a refinement is **started**, the job’s status is set to **running** so it appears in the dashboard and can be tracked.
- When the refinement **completes** or **fails**, the job’s status is restored to its previous value (e.g. `completed` or `failed`).

## Studio UI

- **Masthead:** Split layout — left: white area with Red Hat logo (red) and “AI Crew”; right: red bar with project breadcrumb, search, notifications.
- **Sidebar:** Admin user with avatar (Red Hat red circle + initial) and email.
- **Files page:** Project dropdown reloads the file tree immediately for the selected project; no need to wait for the next poll.

## UI testing (Cypress)

- **Component tests** (`studio-ui/cypress/component/`): Files (including project dropdown file reload), AppLayout (logo, avatar, masthead), Dashboard, Tasks, Agents.
- **Fixtures:** `files.json` (job-001), `files-job-002.json` (job-002) for project-switch test.
- Run: `cd studio-ui && npm run cy:component`. See `studio-ui/cypress/README.md` for coverage.
