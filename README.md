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

### Option 4: Containerized Deployment with Podman

```bash
# 1. Build the container image
podman build -t crew-ai-software:latest -f Containerfile .

# 2. Run with Web UI (accessible at http://localhost:8080)
podman run -d \
  --name crew-studio \
  -p 8080:8080 \
  -v ~/.crew-ai:/root/.crew-ai:ro \
  crew-ai-software:latest

# 3. Or run CLI commands directly
podman run --rm \
  -v ~/.crew-ai:/root/.crew-ai:ro \
  crew-ai-software:latest \
  python -m src.llamaindex_crew.main "Build a calculator"
```

## 📊 Professional Dashboard

Access the web UI at `http://localhost:8080` to:

- ✅ Monitor task completion percentages in real-time
- 📈 Track phase-by-phase progress (Meta → Product Owner → Designer → Tech Architect → Dev → Frontend)
- 📋 View Kanban-style task board (To Do, In Progress, Completed)
- 📁 Browse generated files and code
- 💰 Monitor budget and API costs

**Start the UI:**
```bash
# From the root directory
make studio-run

# Or manually
cd crew_studio
export PYTHONPATH=$(pwd)/../agent:$(pwd)/../agent/src:$PYTHONPATH
python3 llamaindex_web_app.py
```

## 📚 Documentation

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
│   │       ├── workflows/     # Software development workflow
│   │       ├── orchestrator/  # State machine & task manager
│   │       ├── tools/         # File, Git, Test runner tools
│   │       └── config/        # Secure configuration module
│   ├── docs/                  # Documentation (MkDocs)
│   └── tests/                 # Comprehensive test suite
├── crew_studio/               # Web UI Dashboard
│   ├── static/               # React-based frontend
│   └── templates/            # HTML templates
├── Containerfile             # Podman/OCI container definition
└── Makefile                  # Build & run automation
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

## 🐳 Deployment

### Podman Compose (Multi-Service)

```bash
# Start all services
cd agent
podman-compose up -d

# Services:
# - Crew Studio UI: http://localhost:8080
# - PostgreSQL: localhost:5432
# - Redis (Dragonfly): localhost:6379
# - RabbitMQ: localhost:5672, UI at http://localhost:15672
```

### OpenShift/Kubernetes

```bash
# Build and push to registry
podman build -t quay.io/youruser/crew-ai-software:latest -f Containerfile .
podman push quay.io/youruser/crew-ai-software:latest

# Deploy to OpenShift
oc new-app quay.io/youruser/crew-ai-software:latest \
  --name=crew-ai \
  -e CONFIG_FILE_PATH=/config/crew.config.yaml

# Create config from secret
oc create secret generic crew-config --from-file=crew.config.yaml
oc set volume deployment/crew-ai --add \
  --type=secret \
  --secret-name=crew-config \
  --mount-path=/config
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
