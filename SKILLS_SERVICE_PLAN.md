# Skills Service & Configurable Tool Registry

> **Note**: The Skills Service has been extracted to a standalone repository:
> [varkrish/skills-service](https://github.com/varkrish/skills-service).
> It is no longer part of the `opl-ai-software-team` repo. Set `SKILLS_SERVICE_DIR`
> in your `.env` to point to your local checkout (defaults to `../skills-service`).

## Overview

Two features, cleanly separated:

1. **Skills Service** — standalone FastAPI microservice (separate repo) that indexes Cursor-style skill folders (`skill-name/SKILL.md`) and provides semantic search over them. Flat structure with tag-based filtering via YAML frontmatter.
2. **Tool Registry** — config-driven mechanism to declare extra tools (native Python or MCP server) per agent type via `config.yaml`. Agents pick up tools at startup without code changes.

---

## Part A: Skills Service (standalone repo: `skills-service`)

### Skill Folder Structure

Skills follow the Cursor convention. Each skill is a flat folder containing a `SKILL.md` with YAML frontmatter:

```
skills/
  frappe-api-patterns/
    SKILL.md              # required
    reference.md          # optional
  react-component-style/
    SKILL.md
  kubernetes-deployment/
    SKILL.md
    scripts/
      validate.sh
```

**SKILL.md frontmatter:**

```yaml
---
name: frappe-api-patterns
description: >-
  Frappe/ERPNext API patterns including whitelisted methods, DocType CRUD,
  and hooks placement.
tags: [python, frappe, erp]
---
```

- `name` — unique identifier (lowercase, hyphens, max 64 chars). Defaults to directory name.
- `description` — what + when (used for semantic matching AND display).
- `tags` — optional labels for filtering queries.
- Additional `.md` files in the folder are indexed alongside `SKILL.md`.

### Discovery & Indexing

- **Discovery** (`discovery.py`): scans `SKILLS_BASE_DIR` for `*/SKILL.md` patterns, parses frontmatter.
- **Cache invalidation**: SHA-256 content hash of all file paths + contents. Deterministic across container restarts and bind mounts.
- **Single unified index**: all skills indexed into one `VectorStoreIndex` with per-document metadata (`skill_name`, `tags`, `source_file`).
- **Embedding model**: `BAAI/bge-small-en-v1.5` (local, no API key).

### API Endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `GET /health` | GET | Basic liveness |
| `GET /health/ready` | GET | 200 when index built, 503 otherwise |
| `GET /skills` | GET | List all discovered skills with metadata |
| `POST /query` | POST | Semantic search: `{query, top_k, tags?}` |
| `POST /reload` | POST | Async re-index (returns 202) |

### Configuration (env vars)

- `SKILLS_BASE_DIR` — root dir containing skill folders (default: `/app/skills`)
- `SKILLS_INDEX_CACHE_DIR` — persisted index location (default: `~/.crew-ai/skill_index_cache`)
- `PORT` — default 8090

---

## Part B: Tool Registry

### Config Schema (`secure_config.py`)

Two tool source types added to `SecretConfig`:

- **`NativeToolEntry`**: import a Python module, call a factory function with config kwargs.
- **`McpToolEntry`**: connect to an MCP server (stdio or SSE transport), discover and wrap its tools as `FunctionTool` instances.

```yaml
tools:
  global_tools:       # added to ALL agents
    - type: native
      module: "llamaindex_crew.tools.skill_tools"
      name: "SkillQueryTool"
      config:
        service_url: "http://skills-service:8090"

  agent_tools:        # per-agent-role
    developer:
      - type: native
        module: "llamaindex_crew.tools.skill_tools"
        name: "SkillQueryTool"
        config:
          service_url: "http://skills-service:8090"
          default_tags: ["python", "backend"]
      - type: mcp
        server_name: "jira"
        command: "python"
        args: ["-m", "crew_jira_connector.server"]
```

### Tool Loader (`tool_loader.py`)

Resolves `ToolEntry` configs into `FunctionTool` instances at agent init time. Dispatches to native import or MCP bridge based on `type`. Failures are logged and skipped — agents always start.

### MCP Bridge (`mcp_bridge.py`)

Connects to an MCP server, discovers tools via `list_tools()`, wraps each as a LlamaIndex `FunctionTool`:

- Tool names prefixed: `mcp_{server_name}_{tool_name}`
- Optional `tools` allow-list filters which MCP tools are exposed
- `inputSchema` → Pydantic model for type-safe agent interactions
- Supports stdio (`command`/`args`) and SSE/HTTP (`url`) transports

### Agent Wiring

`DevAgent` and `FrontendAgent` load extra tools from config at `__init__` time. No changes to `BaseLlamaIndexAgent`.

---

## Part C: Observability (`utils/observability.py`)

- **StructuredFormatter**: JSON log formatter for machine-parseable output
- **TraceContext / Span**: lightweight W3C-compatible trace/span IDs with timing
- **log_tool_call / log_tool_error**: structured logging for every tool invocation
- **Skills service**: JSON-structured logging via custom formatter
- Compatible with OpenTelemetry when available; works standalone without it

---

## Files

| File | Status |
|---|---|
| `skills-service/src/main.py` | Standalone repo — FastAPI app |
| `skills-service/src/discovery.py` | Standalone repo — skill folder scanner |
| `skills-service/src/indexer.py` | Standalone repo — vector index builder |
| `skills-service/src/config.py` | Standalone repo — env var config |
| `skills-service/Containerfile` | Standalone repo — container image |
| `skills-service/pyproject.toml` | Standalone repo — dependencies |
| `agent/src/llamaindex_crew/config/secure_config.py` | Modified — added SkillsConfig, NativeToolEntry, McpToolEntry, ToolsConfig |
| `agent/src/llamaindex_crew/tools/skill_tools.py` | New — SkillQueryTool factory |
| `agent/src/llamaindex_crew/tools/mcp_bridge.py` | New — MCP→FunctionTool bridge |
| `agent/src/llamaindex_crew/tools/tool_loader.py` | New — config-driven tool resolver |
| `agent/src/llamaindex_crew/tools/__init__.py` | Modified — new exports |
| `agent/src/llamaindex_crew/utils/observability.py` | New — structured logging + tracing |
| `agent/src/llamaindex_crew/agents/dev_agent.py` | Modified — loads extra tools from config |
| `agent/src/llamaindex_crew/agents/frontend_agent.py` | Modified — loads extra tools from config |
| `agent/src/llamaindex_crew/config/__init__.py` | Modified — new exports |
| `agent/config.example.yaml` | Modified — skills + tools sections |
| `compose.yaml` | Modified — skills-service (commented) |

## Tests (73 total)

| Test file | Count | Scope |
|---|---|---|
| `test_tool_config.py` | 13 | Config models |
| `test_tool_loader.py` | 9 | Native + MCP tool loading |
| `test_skill_tools.py` | 10 | SkillQueryTool factory |
| `test_mcp_bridge.py` | 7 | MCP bridge |
| `test_agent_tool_wiring.py` | 4 | Agent integration |
| `test_observability.py` | 10 | Structured logging + tracing |
| `test_discovery.py` | 10 | Skill folder scanning |
| `test_api.py` | 10 | Skills service API |

---

## Decisions

- **Flat skills, no domain hierarchy** — follows Cursor skill conventions exactly
- **Tags over domains** — lightweight filtering via YAML frontmatter, not directory nesting
- **Separate service** — independent lifecycle, can be updated/scaled without touching the crew backend
- **Config-driven tool registry** — operators add tools via YAML, not code changes
- **MCP bridge** — any MCP server's tools become agent tools via config
- **HTTP tool, not QueryEngineTool** — keeps embedding model out of crew backend entirely
- **`service_url: null` disables the tool** — zero impact on deployments that don't use skills
- **Graceful degradation everywhere** — tool loading failures are logged and skipped
