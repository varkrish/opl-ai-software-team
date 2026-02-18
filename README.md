# AI Software Development Crew

> **Multi-Agent AI Software Development Team** — Built with LlamaIndex, integrated with Red Hat MaaS, powered by Podman

An intelligent software development crew that transforms your vision into production-ready code using AI agents for requirements analysis, architecture design, development, and testing.

## Key Features

- **Multi-Agent System** — Meta Agent, Product Owner, Designer, Tech Architect, Dev & Frontend crews
- **Task-Level Tracking** — SQLite-backed task management with real-time dashboard
- **Budget Control** — Real-time cost monitoring and limits
- **TDD/BDD Workflow** — Test-Driven Development with Gherkin scenarios
- **Secure Configuration** — File-based config with encryption support
- **Professional UI** — Modern dashboard with phase progress and task tracking
- **Pluggable LLMs** — Works with any OpenAI-compatible API (Red Hat MaaS, vLLM, Ollama, etc.)
- **MTA Migration** — Upload an MTA report and auto-migrate legacy code with per-file issue tracking ([docs](docs/migration.md))

## Quick Start

**Local development:** run `make studio-run` (backend) + `make studio-dev` (frontend) and open http://localhost:3000.
**Containers:** `cp .env.example .env`, create `config.yaml`, then `make compose-up` — UI at http://localhost:3000.

### Prerequisites

- **Python 3.10+**
- **Node.js 18+** (for frontend dev)
- **Podman** (for containerized / OCP deployment)
- **Helm 3** (for OpenShift deployment)
- **LLM API Key** (Red Hat MaaS, or run Ollama locally)

### Option 1: Quick Setup (5 minutes)

```bash
# 1. Clone the repository
git clone https://github.com/varkrish/opl-ai-software-team.git
cd opl-ai-software-team

# 2. Install dependencies
pip install -e ./agent

# 3. Configure your LLM provider
mkdir -p ~/.crew-ai
cp agent/config.example.yaml ~/.crew-ai/config.yaml
chmod 600 ~/.crew-ai/config.yaml
# Edit config with your API key and MaaS endpoint
vim ~/.crew-ai/config.yaml

# 4. Run your first project
cd agent
python -m src.llamaindex_crew.main "Create a simple calculator in Python"
```

### Option 2: Using Red Hat MaaS (Recommended)

```bash
# 1. Set up config for Red Hat MaaS
cat > ~/.crew-ai/config.yaml << 'EOF'
llm:
  api_key: "your_maas_api_key"
  api_base_url: "https://litellm-prod.apps.maas.redhatworkshops.io"
  environment: "production"
  model_manager: "gpt-4o-mini"
  model_worker: "gpt-4o-mini"
  model_reviewer: "gpt-4o-mini"
budget:
  max_cost_per_project: 100.0
EOF
chmod 600 ~/.crew-ai/config.yaml

# 2. Run the crew
cd agent
python -m src.llamaindex_crew.main "Build a REST API for managing books"
```

### Option 3: Free Local Development with Ollama

```bash
# 1. Install and start Ollama
curl -fsSL https://ollama.com/install.sh | sh
systemctl start ollama
ollama pull llama3.2:latest

# 2. Configure for local use
cat > ~/.crew-ai/config.yaml << 'EOF'
llm:
  environment: "local"
  api_key: "not-needed"
  ollama_model: "llama3.2:latest"
EOF
chmod 600 ~/.crew-ai/config.yaml

# 3. Run the crew (100% free)
cd agent
python -m src.llamaindex_crew.main "Create a TODO app"
```

### Option 4: Containerized Deployment (Podman / Docker)

```bash
# 1. Create your LLM config
cp agent/config.example.yaml config.yaml
chmod 600 config.yaml
# Edit config.yaml with your API key and MaaS endpoint

# 2. Copy environment file
cp .env.example .env

# 3. Start the full stack (backend + frontend)
make compose-up
# Or: podman-compose up -d --build

# Frontend: http://localhost:3000
# Backend API: http://localhost:8080
```

To stop: `make compose-down` (or `podman-compose down`).

## Professional Dashboard (Crew Studio)

The Crew Studio UI provides:

- Monitor task completion percentages in real-time
- Track phase-by-phase progress (Meta -> Product Owner -> Designer -> Tech Architect -> Dev -> Frontend)
- View Kanban-style task board (To Do, In Progress, Completed)
- Browse generated files and code (per-project; switching project reloads the file tree)
- Monitor budget and API costs
- **Prompt-based refinement** — After a job completes, use the Refine panel to apply natural-language edits
- **MTA Migration** — Upload MTA reports, run automated Java migration with retry support

**Start the system:**

*Option A — Local development (backend + React dev server):*
```bash
# Terminal 1: start backend (Flask API on port 8080)
make studio-run

# Terminal 2: start frontend (Vite on port 3000)
make studio-dev
```
Then open **http://localhost:3000** in your browser.

*Option B — Containers (single command):*
```bash
make compose-up
```
Then open **http://localhost:3000** (frontend) and **http://localhost:8080** (backend API/health).

## Deployment

### Podman / Docker Compose (Full Stack)

```bash
make compose-up          # Build & start backend + frontend
make compose-down        # Stop
make compose-logs        # Follow logs
make compose-clean       # Stop & remove volumes
```

| Service | URL |
|---------|-----|
| Frontend (Crew Studio UI) | http://localhost:3000 |
| Backend API | http://localhost:8080 |
| Health check | http://localhost:8080/health |

### OpenShift / Kubernetes (Helm)

The project includes a Helm chart at `deploy/helm/crew-studio/` for deploying to OpenShift Container Platform.

**What gets deployed:**

| Resource | Description |
|----------|-------------|
| Backend Deployment | Flask API + AI agents with PVC storage |
| Frontend Deployment | React/PatternFly UI served by Nginx |
| Services | ClusterIP (port 8080) for both |
| Route | OpenShift Route with edge TLS on frontend |
| Secret | `config.yaml` with LLM API key + MaaS endpoint |
| ConfigMap | Operational env vars (paths, Flask env) |
| PVCs | Workspace (5Gi) + data/DB (1Gi) |

**Deploy:**

```bash
# Set your credentials
export LLM_API_KEY=your_maas_api_key
export LLM_API_BASE_URL=https://litellm-prod.apps.maas.redhatworkshops.io

# Full build, push, and deploy
make helm-deploy

# Or deploy with dev overlay (smaller resources, both routes exposed)
make helm-deploy-dev

# Or use Helm directly (images already on Quay.io)
helm upgrade --install crew-studio deploy/helm/crew-studio \
    --namespace crew-studio --create-namespace \
    --set llm.apiKey=$LLM_API_KEY \
    --set llm.apiBaseUrl=$LLM_API_BASE_URL
```

**Manage:**

```bash
make helm-status         # Check release status
make helm-uninstall      # Tear down the release
make oc-logs             # Follow backend logs
```

**Override models or other settings:**

```bash
helm upgrade crew-studio deploy/helm/crew-studio \
    --reuse-values \
    --set llm.modelWorker=granite-3-2-8b-instruct \
    --set llm.maxTokens=4096
```

See `deploy/helm/crew-studio/values.yaml` for all configurable values.

## Project Structure

```
├── agent/                      # Core AI Agent Framework
│   ├── src/
│   │   ├── llamaindex_crew/    # LlamaIndex-based implementation
│   │   │   ├── agents/         # AI Agents (Meta, PO, Designer, etc.)
│   │   │   ├── config/         # Secure configuration module
│   │   │   ├── tools/          # File, Git, Test runner tools
│   │   │   └── utils/          # LLM config, document indexer
│   │   └── ai_software_dev_crew/  # Orchestrator & workflows
│   ├── docs/                   # Documentation (MkDocs)
│   └── tests/                  # Unit, API, and E2E tests
├── crew_studio/                # Backend: Flask API + job DB
│   ├── llamaindex_web_app.py   # Main Flask app
│   ├── job_database.py         # SQLite job/task tracking
│   ├── migration/              # MTA migration engine
│   │   ├── mta_parser.py       # MTA report parser (per-file issue splitting)
│   │   ├── runner.py           # Migration runner with retry logic
│   │   └── blueprint.py        # /api/migration/* endpoints
│   ├── refinement_runner.py    # Prompt-based refinement engine
│   └── build_runner.py         # Build/compile runner
├── studio-ui/                  # Frontend: React + PatternFly (Vite)
│   └── src/                    # Pages, components, API client
├── deploy/
│   └── helm/crew-studio/       # Helm chart for OpenShift/K8s
│       ├── Chart.yaml
│       ├── values.yaml         # Default values (Red Hat MaaS)
│       ├── values-dev.yaml     # Dev/staging overlay
│       └── templates/          # K8s manifests
├── compose.yaml                # Full stack (Podman / Docker)
├── Containerfile.backend       # Backend container (UBI9 + Python 3.11)
├── Containerfile.frontend      # Frontend container (UBI9 + Nginx)
├── .env.example                # Environment template
├── Makefile                    # Build, run, test, deploy targets
└── config.yaml                 # LLM config (not committed — see config.example.yaml)
```

## Supported LLM Providers

This project uses a **pluggable LLM architecture** — any OpenAI-compatible API works:

- **Red Hat MaaS** (Recommended — via LiteLLM gateway)
- **Ollama** (Free, runs on your machine)
- **vLLM** (High-throughput serving)
- **LiteLLM Proxy** (Universal gateway)
- **OpenAI** / **OpenRouter** / **Azure OpenAI** (via OpenAI-compatible interface)
- **Any OpenAI-compatible endpoint**

See [LLM Configuration Guide](agent/docs/guide/llm-configuration.md) for detailed setup.

## Testing

```bash
# Agent framework tests
make agent-test

# Backend API tests
make backend-test-api

# Backend E2E tests
make backend-test-e2e

# All backend tests
make backend-test-all

# Refinement tests
make refine-test
```

**UI (Cypress):**

```bash
cd studio-ui
npm run cy:component   # Component tests
npm run cy:e2e         # E2E tests (requires dev server + backend)
```

## Makefile Targets

| Target | Description |
|--------|-------------|
| `make setup` | Install agent dependencies |
| `make studio-run` | Start backend (Flask on port 8080) |
| `make studio-dev` | Start frontend dev server (Vite on port 3000) |
| `make agent-test` | Run agent framework tests |
| `make backend-test-all` | Run all backend tests |
| `make compose-up` | Build & start full stack via compose |
| `make compose-down` | Stop all compose services |
| `make container-build` | Build backend + frontend images |
| `make helm-deploy` | Build, push, deploy to OpenShift via Helm |
| `make helm-deploy-dev` | Deploy with dev overlay |
| `make helm-status` | Check Helm release status |
| `make helm-uninstall` | Remove Helm release from cluster |
| `make oc-logs` | Follow backend pod logs |

## Documentation

- **[Refinement & Studio UI](docs/REFINEMENT_AND_UI.md)** — Prompt-based refinement, file/project scope, dashboard tracking
- **[Getting Started Guide](agent/docs/getting-started/quickstart.md)** — Detailed setup instructions
- **[LLM Configuration](agent/docs/guide/llm-configuration.md)** — Configure any OpenAI-compatible provider
- **[Secure Config Patterns](agent/docs/deployment/secure-config-patterns.md)** — Production security best practices
- **[Using Ollama](agent/docs/getting-started/ollama.md)** — Free local development setup
- **[Full Agent Documentation](agent/README.md)** — Complete framework documentation

## Contributing

Contributions are welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Add tests for new features
4. Submit a pull request

## License

MIT License — See [LICENSE](LICENSE) for details.

---

**Built with [LlamaIndex](https://www.llamaindex.ai/) | Integrated with [Red Hat MaaS](https://www.redhat.com/) | Containerized with [Podman](https://podman.io/) | Deployed on [OpenShift](https://www.redhat.com/en/technologies/cloud-computing/openshift)**
