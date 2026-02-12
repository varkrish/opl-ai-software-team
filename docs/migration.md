# MTA Report-Driven Code Migration

AI Crew Studio can ingest an MTA (Migration Toolkit for Applications) report and
automatically migrate legacy source code using a two-phase AI pipeline.

## How It Works

### 1. Upload & Configure

- Create a job (or use an existing one).
- Upload the legacy source code into the job workspace.
- Upload the MTA report (JSON, CSV, HTML, YAML, or plain text).

### 2. Start Migration

Call the API or use the Migration UI page:

```bash
POST /api/jobs/<job_id>/migrate
Content-Type: application/json

{
  "migration_goal": "Migrate from JBoss EAP 7 to EAP 8",
  "migration_notes": "Skip files under src/auth/"
}
```

### 3. Two-Phase Pipeline

**Phase 1 — Analysis:** The Analysis Agent reads the MTA report (any format) and
produces a structured `migration_plan.json` with every actionable issue, including
affected files, severity, effort, and specific migration hints.

**Phase 2 — Execution:** For each affected file, the Execution Agent applies the
changes using 4-tier context injection, writes the updated file, and commits a git
snapshot.

### 4. Monitor Progress

```bash
GET /api/jobs/<job_id>/migration       # summary + issues list
GET /api/jobs/<job_id>/migration/plan  # raw migration_plan.json
```

## 4-Tier Context Injection

The migration agents receive guidance through four layers:

| Tier | Source | How to Use |
|------|--------|------------|
| **1. System** | `prompts/migration/apply_changes.txt` | Built-in best practices (import-first, preserve tests, etc.) |
| **2. Repo** | `.migration-rules.md` in workspace root | Add project-specific rules: "Use SLF4J for logging" |
| **3. Uploaded** | Files in `workspace/docs/` | Upload architecture docs, reference guides |
| **4. Per-run** | `migration_notes` in POST body | Runtime instructions: "Skip auth module" |

## Frontend UI

Navigate to `/migration/:jobId` to see:

- Migration goal + notes input form
- Real-time summary badges (pending, running, completed, failed)
- Issues table with expandable detail rows (description, hint, errors)

## File Structure

```
crew_studio/migration/
├── __init__.py
├── blueprint.py      # Flask API endpoints
├── runner.py         # Two-phase orchestration
└── utils.py          # git_snapshot, migration rules loader

agent/src/llamaindex_crew/agents/
└── migration_agent.py  # MigrationAnalysisAgent + MigrationExecutionAgent

agent/src/ai_software_dev_crew/prompts/migration/
├── analyze_report.txt  # System prompt for analysis
└── apply_changes.txt   # System prompt for execution (Tier 1)

studio-ui/src/pages/
└── Migration.tsx       # Frontend migration page

studio-ui/src/api/
└── client.ts           # startMigration, getMigrationStatus, getMigrationPlan
```

## Testing

```bash
# DB layer tests
cd agent && python -m pytest tests/unit/test_migration_db.py -v

# Agent prompt tests
cd agent && python -m pytest tests/unit/test_migration_agent.py -v

# API endpoint tests
cd agent && python -m pytest tests/api/test_migration_endpoint.py -v

# Frontend component tests (requires Cypress)
cd studio-ui && npx cypress run --component --spec cypress/component/Migration.cy.tsx
```
