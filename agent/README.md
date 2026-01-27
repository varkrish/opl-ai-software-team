# AI Software Development Crew

A scalable, multi-agent AI software development team built with CrewAI that follows BDD (Gherkin) and TDD practices, with budget control and manager coordination.

## Features

- 🤖 **Multi-Agent System**: Business Analyst, Developers, QA, DevOps agents
- 📊 **Budget Tracking**: Real-time cost monitoring and limits
- 🧪 **TDD Workflow**: Red-Green-Refactor cycle enforced
- 📝 **BDD Requirements**: Gherkin scenarios for all features
- 🔧 **Custom Tools**: File operations, Git, test runners
- 🐳 **Docker Support**: Easy local development setup
- 🔍 **Code Review**: Review and refine existing codebases automatically

## Quick Start

### 1. Install Dependencies

```bash
# Install uv (recommended) or use pip
pip install uv
uv pip install -e .

# Or with pip
pip install -e .
```

### 2. Configure Environment

#### Option A: Secure Config File (Recommended)

```bash
# Create user config directory
mkdir -p ~/.crew-ai

# Copy example config
cp config.example.yaml ~/.crew-ai/config.yaml

# Set secure permissions (REQUIRED)
chmod 600 ~/.crew-ai/config.yaml

# Edit with your API key
vim ~/.crew-ai/config.yaml

# Configure for your provider:
llm:
  api_key: "your_api_key_here"
  api_base_url: "https://litellm-prod.apps.maas.redhatworkshops.io"
  environment: "production"
```

**See [Configuration Guide](docs/getting-started/configuration.md) for detailed setup**

#### Option B: Local Development (Free, using Ollama)

```bash
# Install Ollama
brew install ollama  # macOS
# or visit https://ollama.com for other platforms

# Start Ollama and pull a model
ollama serve
ollama pull llama3.2:latest

# Configure for local
cp config.example.yaml ~/.crew-ai/config.yaml
chmod 600 ~/.crew-ai/config.yaml
# Edit .env:
llm:
  environment: "local"
  ollama_model: "llama3.2:latest"
```

#### Option C: Environment Variables (Legacy)

```bash
# Still supported for backward compatibility
export LLM_API_KEY=your_key
export LLM_API_BASE_URL=https://your-provider.com/v1
export LLM_ENVIRONMENT=production
```

### 3. Start Infrastructure (Optional)

#### Option A: Using Podman Container (Recommended)

Build and run the application in a container:

```bash
# Build the container image
podman build -t crew-ai-software:latest -f Containerfile .

# Run the web UI (accessible at http://localhost:8080)
podman run -d -p 8080:8080 --name crew-ai-web \
  -v ~/.crew-ai:/root/.crew-ai:ro \
  crew-ai-software:latest

# Run CLI commands
podman run --rm \
  -v ~/.crew-ai:/root/.crew-ai:ro \
  crew-ai-software:latest \
  python -m src.llamaindex_crew.main "Build a calculator app"

# Run in interactive mode
podman run -it --rm \
  -v ~/.crew-ai:/root/.crew-ai:ro \
  crew-ai-software:latest bash
```

**Container Features:**
- ✅ Pre-built with all dependencies
- ✅ Includes build tools for C extensions
- ✅ Production-ready configuration
- ✅ Health checks enabled
- ✅ Web UI on port 8080
- ✅ Red Hat Universal Base Image (UBI) based

#### Option B: Using Podman Compose

For distributed setup with cache/queue using Podman:

```bash
podman-compose up -d
```

This starts:
- **Dragonfly** (Redis-compatible cache) on port 6379
- **RabbitMQ** (message queue) on port 5672, management UI on 15672
- **PostgreSQL** (database) on port 5432
- **Web UI** on port 8080
- **Crew CLI** (access via `podman exec -it crew bash`)

#### Option C: Deploy to OpenShift

```bash
# Build and push to Quay.io (Red Hat's container registry)
podman build -t quay.io/youruser/crew-ai-software:latest -f Containerfile .
podman push quay.io/youruser/crew-ai-software:latest

# Deploy to OpenShift
oc new-app quay.io/youruser/crew-ai-software:latest \
  --name=crew-ai-studio \
  -e CONFIG_FILE_PATH=/config/crew.config.yaml

# Expose the service
oc expose svc/crew-ai-studio

# Create config from secret
oc create secret generic crew-config --from-file=crew.config.yaml
oc set volume deployment/crew-ai-studio --add \
  --type=secret \
  --secret-name=crew-config \
  --mount-path=/config

# Check status
oc get pods -l app=crew-ai-studio
oc logs -f deployment/crew-ai-studio
```

### 4. Run the Crew

#### Option A: Using Secure Config File (Recommended)

```bash
# With auto-detected config (~/.crew-ai/config.yaml)
python -m llamaindex_crew.main "Build a simple TODO API with FastAPI"

# Or specify config explicitly
python -m llamaindex_crew.main \
  --config ~/.crew-ai/config.yaml \
  "Build a calculator API"
```

#### Option B: Using Environment Variables (Legacy)

```bash
# Set API key
export LLM_API_KEY=your_key

# Run
python -m llamaindex_crew.main "Build a calculator API"
```

#### Code Review Mode (Review Existing Codebase)

```bash
# Review an existing codebase
uv run ai_software_dev_crew /path/to/your/codebase

# Or use the review flag
uv run ai_software_dev_crew --review /path/to/your/codebase

# Review current directory
uv run ai_software_dev_crew .
```

**See [CODE_REVIEW_GUIDE.md](CODE_REVIEW_GUIDE.md) for detailed code review documentation.**

## How It Works

### 1. Business Analysis Phase

The **BA Crew** analyzes your vision and creates:
- User stories
- Gherkin scenarios (Given-When-Then)
- Acceptance criteria
- Technical requirements

### 2. Development Phase

The **Dev Crew** implements features using TDD:
1. **RED**: Write failing test
2. **GREEN**: Write minimal code to pass
3. **REFACTOR**: Improve code quality
4. **COMMIT**: Save to git

### 3. Code Review

The **Code Reviewer** checks:
- Test coverage (>= 80%)
- Code quality
- Best practices
- Security

## Project Structure

```
ai_software_dev_crew/
├── src/
│   ├── ai_software_dev_crew/      # Legacy CrewAI implementation
│   │   ├── budget/                # Budget tracking
│   │   ├── crews/                 # Specialized crews
│   │   ├── tools/                 # Custom tools
│   │   └── orchestrator/          # Workflow coordination
│   └── llamaindex_crew/           # ✨ New LlamaIndex implementation
│       ├── agents/                # LlamaIndex agents (Meta, PO, Designer, etc.)
│       ├── workflows/             # Software development workflow
│       ├── orchestrator/          # State machine & task management
│       ├── tools/                 # File, Git, and test runner tools
│       ├── utils/                 # LLM config, prompt loader, indexer
│       └── web/                   # Flask web UI
├── tests/
│   ├── unit/                      # Fast unit tests
│   ├── integration/               # Integration tests
│   ├── e2e/                       # ✨ End-to-end tests
│   │   ├── test_workflow_e2e.py  # Workflow E2E tests
│   │   ├── test_web_api_e2e.py   # API E2E tests
│   │   └── test_web_ui_playwright.py  # UI E2E tests
│   ├── api/                       # API tests
│   ├── frontend/                  # Frontend tests
│   └── conftest.py                # Pytest configuration
├── workspace/                     # Generated code output
├── pytest.ini                     # Pytest configuration
├── docker-compose.yml             # Infrastructure setup
└── pyproject.toml                 # Dependencies
```

## Available Tools

### File Operations
- `FileWriterTool`: Write files to workspace
- `FileReaderTool`: Read files from workspace
- `FileListTool`: List directory contents

### Git Operations
- `GitInitTool`: Initialize repository
- `GitCommitTool`: Commit changes
- `GitStatusTool`: Check status
- `GitLogTool`: View history

### Testing
- `PytestRunnerTool`: Run tests
- `CodeCoverageTool`: Measure coverage

## Configuration

### Secure Configuration (Recommended)

Use config files with file permissions for security:

```yaml
# ~/.crew-ai/config.yaml (chmod 600)
llm:
  api_key: "your_api_key_here"
  api_base_url: "https://your-provider.com/v1"
  environment: "production"
  
budget:
  max_cost_per_project: 100.0
  max_cost_per_hour: 10.0
```

**Configuration Priority:**
1. `--config` CLI argument
2. `CONFIG_FILE_PATH` environment variable
3. `./crew.config.yaml` (project)
4. `~/.crew-ai/config.yaml` (user)
5. `/etc/crew-ai/config.yaml` (system)
6. Docker/Kubernetes secrets
7. Environment variables (legacy)

### Environment Variables (Legacy)

```bash
# Generic configuration (works with any OpenAI-compatible provider)
LLM_API_KEY=your_api_key
LLM_API_BASE_URL=https://your-provider.com/v1  # Optional

# Budget Limits
BUDGET_MAX_COST_PER_PROJECT=100.00
BUDGET_MAX_COST_PER_HOUR=10.00
BUDGET_ALERT_THRESHOLD=0.8

# Workspace
WORKSPACE_PATH=./workspace
LOG_LEVEL=INFO
PROJECT_ID=my-project
```

**See [Configuration Guide](docs/getting-started/configuration.md) and [Secure Config Patterns](docs/deployment/secure-config-patterns.md)**

## Examples

### Example 1: Simple API

```bash
run_crew "Build a REST API for managing books with CRUD operations"
```

### Example 2: Calculator

```bash
run_crew "Create a calculator module with add, subtract, multiply, divide functions"
```

### Example 3: User Authentication

```bash
run_crew "Implement user registration and login with JWT tokens"
```

## Budget Tracking

The system tracks costs in real-time:

```
💰 Budget Report
  Total Cost: $0.1234
  Budget Limit: $100.00
  Budget Used: 0.1%
  Remaining: $99.88

  Cost by Agent:
    - business_analyst: $0.0456
    - backend_developer: $0.0678
    - code_reviewer: $0.0100
```

## Development

### Running Tests

The project has a comprehensive test suite organized by test type:

```bash
# Install test dependencies
pip install -e ".[test]"

# Run all tests
pytest

# Run by test category
pytest -m unit          # Fast unit tests only
pytest -m integration   # Integration tests
pytest -m e2e          # End-to-end tests (slow, requires API keys)
pytest -m api          # API tests only
pytest -m ui           # UI tests only (requires Playwright)

# Run specific test files
pytest tests/unit/
pytest tests/integration/
pytest tests/e2e/

# Skip slow tests
pytest -m "not slow"

# Run with coverage
pytest --cov=src --cov-report=html
```

#### Test Categories

| Category | Speed | Cost | Requirements |
|----------|-------|------|--------------|
| **Unit** | Fast (< 1s) | Free | None |
| **Integration** | Medium (< 30s) | Free | DB/Filesystem |
| **E2E** | Slow (5-10min) | $1-2 | API Keys |
| **API** | Fast (< 30s) | Free | None |
| **UI** | Medium (< 2min) | Free | Playwright |

#### End-to-End Tests

E2E tests verify the complete workflow from vision to code generation:

```bash
# Run E2E tests (requires OPENROUTER_API_KEY or OPENAI_API_KEY)
pytest tests/e2e/ -m e2e

# Run specific E2E test
pytest tests/e2e/test_workflow_e2e.py::test_calculator_workflow_e2e

# Skip slow E2E tests
pytest tests/e2e/ -m "e2e and not slow"
```

**Note**: E2E tests use real LLM APIs and will incur costs. Set up `.env` with your API keys.

See [tests/e2e/README.md](tests/e2e/README.md) for detailed E2E test documentation.

#### UI Tests with Playwright

```bash
# Install Playwright browsers
playwright install chromium

# Run UI tests
pytest tests/e2e/test_web_ui_playwright.py -m ui
```

### Adding New Crews

1. Create new crew file in `src/ai_software_dev_crew/crews/`
2. Define agents and tasks using `@agent` and `@task` decorators
3. Add to orchestrator workflow

### Adding New Tools

1. Create tool in `src/ai_software_dev_crew/tools/`
2. Inherit from `BaseTool`
3. Implement `_run()` method
4. Add to agent's tools list

## Troubleshooting

### "Budget exceeded" error
- Check your `BUDGET_MAX_COST_PER_PROJECT` setting
- Review cost breakdown in budget report
- Increase limits if needed

### "Not a git repository" error
- The workspace will auto-initialize git
- Or manually run: `cd workspace && git init`

### Tools not working
- Ensure `WORKSPACE_PATH` is set correctly
- Check file permissions
- Verify tools are imported in agent definition

## Architecture

```
User Vision
    ↓
BA Crew (Sequential)
    ├── Business Analyst → Requirements
    └── Validator → Approved Requirements
    ↓
Dev Crew (Sequential)
    ├── Backend Developer → Implementation (TDD)
    └── Code Reviewer → Quality Check
    ↓
Results + Budget Report
```

## LlamaIndex Migration ✨

The project has been migrated to use **LlamaIndex** for improved performance and cost-efficiency:

### Key Features
- ✅ **ReActAgent** for sequential task execution
- ✅ **Budget tracking** with token usage monitoring
- ✅ **State machine** for workflow management
- ✅ **Task validation** with SQLite persistence
- ✅ **RAG support** with document indexing
- ✅ **Generic LLM configuration** works with any OpenAI-compatible provider
- ✅ **Local Ollama support** for free development
- ✅ **Comprehensive E2E tests** with pytest & Playwright

### Migration Highlights
- **20x cost reduction** using OpenRouter free models
- **Async agent execution** for better performance
- **Proper error recovery** with state rollback
- **Enhanced budget control** with max iterations
- **Tool integration** (File, Git, Test Runner)

See [MIGRATION_COMPLETE.md](MIGRATION_COMPLETE.md) for full migration details.

## Future Enhancements

- [ ] QA Crew for comprehensive testing
- [ ] DevOps Crew for deployment
- [ ] Kubernetes deployment
- [ ] Distributed execution with RabbitMQ
- [ ] Integration with CI/CD pipelines
- [ ] Support for more LLM providers
- [ ] Advanced RAG features
- [ ] Multi-language support (Go, Rust, etc.)

## License

MIT

## Contributing

Contributions welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Add tests for new features
4. Submit a pull request

## Support

For issues and questions:
- GitHub Issues: [Create an issue]
- Documentation: See `AI_CODING_ECOSYSTEM_SETUP.md`

---

Built with ❤️ using [CrewAI](https://www.crewai.com/)
