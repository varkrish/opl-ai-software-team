# AI Software Development Crew

> 🤖 **Multi-Agent AI Software Development Team** - Built with LlamaIndex, integrated with Red Hat MaaS, powered by Podman

An intelligent software development crew that transforms your vision into production-ready code using AI agents for requirements analysis, architecture design, development, and testing.

## ✨ Key Features

- 🤖 **Multi-Agent System** - Meta Agent, Product Owner, Designer, Tech Architect, Dev & Frontend crews
- 🎯 **Task-Level Tracking** - SQLite-backed task management with real-time dashboard
- 💰 **Budget Control** - Real-time cost monitoring and limits
- 🧪 **TDD/BDD Workflow** - Test-Driven Development with Gherkin scenarios
- 🔐 **Secure Configuration** - File-based config with encryption support
- 🌐 **Professional UI** - Modern dashboard with phase progress and task tracking
- 🔌 **Pluggable LLMs** - Works with any OpenAI-compatible API (Red Hat MaaS, vLLM, Ollama, etc.)

## 🚀 Quick Start

**Start the full system (local):** run the backend with `make studio-run`, then in another terminal run `make studio-dev`, and open http://localhost:3000.  
**Or use containers:** `cp .env.example .env` then `make compose-up` — UI at http://localhost:3000.

### Prerequisites

- **Python 3.10+**
- **Podman** (for containerized deployment)
- **LLM API Key** (Red Hat MaaS, OpenRouter, or run Ollama locally)

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

# Edit config with your API details
vim ~/.crew-ai/config.yaml

# 4. Run your first project
cd agent
python -m src.llamaindex_crew.main "Create a simple calculator in Python"
```

### Option 2: Using Red Hat MaaS (Recommended for Enterprise)

```bash
# 1. Set up config for Red Hat MaaS
cat > ~/.crew-ai/config.yaml << EOF
llm:
  api_key: "your_maas_api_key"
  api_base_url: "https://litellm-prod.apps.maas.redhatworkshops.io"
  environment: "production"
  models:
    manager: "deepseek-r1-distill-qwen-14b"
    worker: "qwen3-14b"
    reviewer: "granite-3-2-8b-instruct"
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
# For RHEL/Fedora:
curl -fsSL https://ollama.com/install.sh | sh
systemctl start ollama

# Pull a model
ollama pull llama3.2:latest

# 2. Configure for local use
cat > ~/.crew-ai/config.yaml << EOF
llm:
  environment: "local"
  ollama_model: "llama3.2:latest"
EOF

chmod 600 ~/.crew-ai/config.yaml

# 3. Run the crew (100% free!)
cd agent
python -m src.llamaindex_crew.main "Create a TODO app"
```

### Option 4: Containerized Deployment (Podman / Docker)

```bash
# 1. Copy environment file and set API keys
cp .env.example .env
# Edit .env with your OPENAI_API_KEY, ANTHROPIC_API_KEY, or OPENROUTER_API_KEY

# 2. Optional: mount LLM config (e.g. for Red Hat MaaS)
# Set CONFIG_FILE in .env to your config path; it will be mounted into the backend.

# 3. Start the full stack (backend + frontend)
make compose-up
# Or: podman-compose up -d --build

# Frontend: http://localhost:3000
# Backend API: http://localhost:8080
```

To stop: `make compose-down` (or `podman-compose down`).

## 📊 Professional Dashboard (Crew Studio)

The Crew Studio UI gives you:

- ✅ Monitor task completion percentages in real-time
- 📈 Track phase-by-phase progress (Meta → Product Owner → Designer → Tech Architect → Dev → Frontend)
- 📋 View Kanban-style task board (To Do, In Progress, Completed)
- 📁 Browse generated files and code (per-project; switching project reloads the file tree)
- 💰 Monitor budget and API costs
- ✨ **Prompt-based refinement** – After a job completes, use the Refine panel to apply natural-language edits (e.g. “add comments”, “delete unused file”) at file or project scope. Refinement runs show as **running** in the dashboard and are tracked until complete.

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
cp .env.example .env   # edit .env with API keys if needed
make compose-up
```
Then open **http://localhost:3000** (frontend) and **http://localhost:8080** (backend API/health).

## 📚 Documentation

- **[Refinement & Studio UI](docs/REFINEMENT_AND_UI.md)** - Prompt-based refinement, file/project scope, dashboard tracking, UI testing
- **[Getting Started Guide](agent/docs/getting-started/quickstart.md)** - Detailed setup instructions
- **[LLM Configuration](agent/docs/guide/llm-configuration.md)** - Configure any OpenAI-compatible provider
- **[Secure Config Patterns](agent/docs/deployment/secure-config-patterns.md)** - Production security best practices
- **[Using Ollama](agent/docs/getting-started/ollama.md)** - Free local development setup
- **[Health Checks](agent/docs/deployment/health-checks.md)** - Monitoring and observability
- **[Full Agent Documentation](agent/README.md)** - Complete framework documentation

## 🏗️ Project Structure

```
├── agent/                      # Core AI Agent Framework
│   ├── src/
│   │   └── llamaindex_crew/   # LlamaIndex-based implementation
│   │       ├── agents/        # AI Agents (Meta, PO, Designer, etc.)
│   │       ├── backends/      # Pluggable backends (OPL crew, Aider)
│   │       ├── workflows/     # Software development workflow
│   │       ├── orchestrator/  # State machine & task manager
│   │       ├── tools/         # File, Git, Test runner tools
│   │       └── config/        # Secure configuration module
│   ├── docs/                  # Documentation (MkDocs)
│   └── tests/                 # Comprehensive test suite
├── crew_studio/               # Backend: Flask API + job DB
│   └── llamaindex_web_app.py  # Serves API and (in prod) static frontend
├── studio-ui/                 # Frontend: React + PatternFly (Vite)
│   ├── src/                   # Pages, components, API client
│   └── public/
├── compose.yaml               # Full stack (backend + frontend)
├── Containerfile.backend      # Backend container
├── Containerfile.frontend    # Frontend container (Nginx)
├── .env.example               # Environment template
└── Makefile                   # Build & run (setup, studio-run, studio-dev, compose-up)
```

## 🔌 Supported LLM Providers

This project uses a **pluggable LLM architecture** - any OpenAI-compatible API works out of the box:

### Cloud Providers
- ✅ **Red Hat MaaS** (Recommended for Enterprise)
- ✅ **OpenRouter** (Access to 200+ models)
- ✅ **OpenAI** (GPT-4, GPT-3.5-turbo)
- ✅ **Anthropic** (Claude via OpenAI proxy)
- ✅ **Google** (Gemini via proxy)
- ✅ **Any OpenAI-compatible API**

### Self-Hosted/Local
- ✅ **Ollama** (Free, runs on your machine)
- ✅ **vLLM** (High-throughput serving)
- ✅ **LocalAI** (Multi-model local server)
- ✅ **LiteLLM Proxy** (Universal gateway)
- ✅ **Text Generation WebUI** (Oobabooga)
- ✅ **llama.cpp Server** (Lightweight C++ implementation)

See [LLM Configuration Guide](agent/docs/guide/llm-configuration.md) for detailed setup.

## 🧪 Testing

```bash
# Run all tests
make test

# Run specific test categories
make test-unit          # Fast unit tests
make test-integration   # Integration tests
make test-e2e          # End-to-end tests (requires API key)

# Or with pytest directly
cd agent
pytest tests/unit/           # Unit tests
pytest tests/e2e/ -m e2e    # E2E tests
```

**UI (Cypress):**

```bash
cd studio-ui
npm run cy:component   # Component tests (Files, Dashboard, AppLayout, etc.)
npm run cy:e2e        # E2E tests (requires dev server + backend)
```

See `studio-ui/cypress/README.md` for what’s covered (e.g. project dropdown file reload, masthead/logo, refinement panel).

## 🐳 Deployment

### Podman / Docker Compose (Full Stack)

```bash
# From repo root: start backend + frontend
make compose-up
# or: podman-compose up -d --build

# Services:
# - Frontend (Crew Studio UI): http://localhost:3000
# - Backend API: http://localhost:8080
# - Health: http://localhost:8080/health
```

### OpenShift / Kubernetes

```bash
# Build and push both images
make container-build
podman push quay.io/youruser/crew-backend:latest
podman push quay.io/youruser/crew-frontend:latest

# Or use the Makefile target (builds and pushes)
make oc-deploy

# Manual deploy: create backend and frontend from images, expose routes as needed.
```

See [Secure Config Patterns](agent/docs/deployment/secure-config-patterns.md) for production deployment.

## 💡 Examples

```bash
# Simple calculator
python -m src.llamaindex_crew.main "Create a calculator module with basic operations"

# REST API
python -m src.llamaindex_crew.main "Build a REST API for managing books with CRUD operations"

# Web application
python -m src.llamaindex_crew.main "Create a TODO list web app with React frontend"

# With specific project ID
python -m src.llamaindex_crew.main "Build a chat bot" --project-id "chatbot-v1"
```

## 🤝 Contributing

Contributions are welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Add tests for new features
4. Submit a pull request

## 📄 License

MIT License - See [LICENSE](LICENSE) for details

## 🆘 Support

- **Issues**: [GitHub Issues](https://github.com/varkrish/opl-ai-software-team/issues)
- **Documentation**: [Full Docs](agent/docs/)
- **Examples**: See [agent/README.md](agent/README.md)

---

**Built with ❤️ using [LlamaIndex](https://www.llamaindex.ai/) | Powered by [Red Hat MaaS](https://www.redhat.com/) | Containerized with [Podman](https://podman.io/)**
