# Release checklist — OPL Crew Backend

Use this for production releases of `opl-ai-software-team` (backend image: `quay.io/varkrish/crew-backend`).

## Pre-release

1. **Unit tests** (from repo root):
   ```bash
   make ci-install
   make test-quick
   make backend-test-api
   ```
2. **Tech Architect smoke test** (in running prod/dev container, with a real job workspace):
   ```bash
   podman exec crew-backend-prod \
     env JOB_ID=<job-uuid> CONFIG_FILE_PATH=/tmp/config.yaml \
     /opt/app-root/bin/python3 /app/crew_studio/test_tech_architect.py
   ```
   Expect: `Production validator OK — N source file(s)` with N ≥ 4 (typically 20+ for multi-service apps).
3. **LLM settings** — confirm Settings → LLM in the UI matches MaaS endpoint (`user_llm_configs` in `crew_jobs.db`). Sync fallback YAML if needed:
   ```bash
   # Host config (mounted read-only at /app/config.yaml)
   # Update ~/.crew-ai/config.yaml from Settings, then:
   podman compose restart backend
   ```
4. Update `CHANGELOG.md` and bump `agent/pyproject.toml` version to match the tag.

## Tag and publish

```bash
# In opl-ai-software-team submodule
git add -A
git commit -m "release: v2.2.0 — tech architect file-tree validation and approved solution contract"
git tag -a v2.2.0 -m "v2.2.0 — see CHANGELOG.md"
git push origin main
git push origin v2.2.0
```

GitHub Actions builds and pushes `quay.io/varkrish/crew-backend:v2.2.0` and `:latest` (see `.github/workflows/`).

## Mono repo bump

```bash
cd opl-crew-mono   # or your prod checkout
git submodule update --remote opl-ai-software-team
git add opl-ai-software-team
git commit -m "chore: bump opl-ai-software-team to v2.2.0"
git push
```

## Prod deploy

```bash
cd /path/to/opl-crew
podman pull quay.io/varkrish/crew-backend:v2.2.0
# Pin image in compose.yml or:
podman compose up -d --pull always backend
podman compose restart backend
curl -sf http://localhost:8280/health
```

## Post-release verification

| Check | Command |
|-------|---------|
| Health | `curl localhost:8280/health` |
| LLM config API | `curl localhost:8280/api/llm/config` |
| Submit smoke job | `curl -X POST localhost:8280/api/jobs -H 'Content-Type: application/json' -d '{"vision":"Build a hello-world CLI"}'` |
| Tech stack gate | Job reaches development with `file_creation` tasks > 0 |

## Rollback

```bash
podman compose stop backend
# Edit compose.yml image to previous tag, e.g. v2.1.0
podman compose up -d backend
```
