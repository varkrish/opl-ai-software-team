# Context Memory Plane Architecture & Schema

The Context Memory Plane persists job records, check-level validation outcomes, intact JSONB artifacts, call-graph edges, and prose vector embeddings to enable cross-job reuse across container restarts and multi-replica backend deployments.

## Database & Storage Setup

- **Database Name**: `crew_context`
- **Postgres Server**: `crew-memmachine-postgres` (image `pgvector/pgvector:pg16`, published `0.0.0.0:55432`)
- **Environment Variable**: `CREW_DOC_INDEX_DSN`
  - Container default: `postgresql+psycopg://memmachine:memmachine_password@crew-memmachine-postgres:5432/crew_context`
  - Host dev fallback: `postgresql://memmachine:memmachine_password@127.0.0.1:55432/crew_context`
- **Local Disk Path Removed**: `~/.crew/doc_index` is obsolete and no longer used. If the database is unreachable, retrieval logs error loudly and returns empty results (fail-closed, fail-open for pipeline execution).

---

## Schema Overview (`crew_context`)

### 1. `jobs`
Relational table tracking job identity and metadata:
- `job_id` (TEXT PRIMARY KEY): Unique job UUID.
- `scope_org`, `scope_project`, `scope_domain` (TEXT): Memory scope partitions.
- `vision`, `tech_stack`, `capability_profile` (TEXT): Job specifications.
- `status` (TEXT): Terminal execution status.
- `created_at` (TIMESTAMPTZ): Job record creation timestamp.

### 2. `job_outcomes`
Check-level validation results:
- `id` (BIGSERIAL PRIMARY KEY)
- `job_id` (TEXT FK -> jobs)
- `check_name` (TEXT): E.g. `wiring_contract`, `entrypoint`, `client_endpoint_alignment`, `pytest`, `smoke`.
- `severity` (TEXT): `error`, `warning`, `info`.
- `passed` (BOOLEAN): True if check passed cleanly.
- `file_path`, `description` (TEXT): Diagnostic failure context.
- `iterations` (INT), `converged` (BOOLEAN): Fix loop metrics.

### 3. `artifacts`
Intact JSONB and text documents:
- `doc_type` (TEXT): `wiring_contract`, `stack_manifest`, `creation_manifest`, `api_contract`, `solution_spec`.
- `content_json` (JSONB): Structured artifact saved whole (not fragmented).
- `content_text` (TEXT): Unstructured prose.

### 4. `prompts`
Prompt strings keyed to produced artifacts (relational, un-embedded).

### 5. `call_graph_edges`
Topological dependency edges captured via `tldr warm` and `read_call_graph`:
- `from_file`, `from_func`, `to_file`, `to_func` (TEXT).

---

## Artifact Seeding Mechanics

Deterministic pipeline functions in `llamaindex_crew.memory.artifact_seeder`:
- `seed_wiring_contract_from_prior(scope, vision, stack)`: Sourced strictly from jobs whose `wiring_contract`, `entrypoint`, and `client_endpoint_alignment` checks passed. Prevents tests-only contracts (job 1cec01ad failure).
- `seed_creation_manifest_from_prior(scope, vision, stack)`: Candidate file list from validated prior jobs.
- `seed_test_plan_from_prior(scope, vision, stack)`: Test plan string including verified `preview_command`.
- `seed_contract_deps_from_prior_callgraph(scope, vision, stack)`: Contract `deps` populated from prior call-graph edges.

---

## Agent Tools

Registered in `DevAgent` when ReAct mode is active (`llamaindex_crew.tools.context_tools`):
- `find_similar_solutions(vision, stack, limit)`: Returns candidate solution specs along with check-level validation outcomes.
- `get_prior_artifact(job_id, name)`: Returns whole intact JSONB/text artifact with validation header.
- `find_reference_implementation(role, stack)`: Fails closed — returns file list only from jobs that passed all validation checks.
- `find_fix_precedent(check_name, error)`: Recalls how past jobs resolved check failures.
- `check_known_bad(proposal)`: Validates proposed contract/layout against recorded anti-patterns (tests-only contract, invalid dependencies, route mismatches).
