# Quick Start

Get started with AI Software Development Crew in minutes!

## Prerequisites

- Python 3.10 or higher
- pip or uv package manager
- **Either:** LLM API key (Red Hat MaaS, OpenRouter, OpenAI) **OR** Ollama for free local development

## Installation

### Option 1: Using Make (Recommended)

```bash
# Clone the repository
git clone https://github.com/varkrish/varkrish-crewai-opl-coder.git
cd crew-coding-bots

# Install dependencies
make install-dev
```

### Option 2: Using pip

```bash
# Install from source
pip install -e .

# Or with test dependencies
pip install -e ".[test]"
```

## Configuration

Choose one of these options:

### Option A: Cloud API (Recommended for Production)

Create a config file:

```bash
mkdir -p ~/.crew-ai
cat > ~/.crew-ai/config.yaml << EOF
llm:
  environment: "production"
  api_key: "your_api_key_here"
  api_base_url: "https://litellm-prod.apps.maas.redhatworkshops.io"
  model_worker: "gpt-4o-mini"
EOF
chmod 600 ~/.crew-ai/config.yaml
```

### Option B: Ollama (Free for Development)

Install and configure Ollama:

```bash
# Install Ollama
brew install ollama  # macOS
# or visit https://ollama.com

# Start Ollama
ollama serve

# Pull a model
ollama pull llama3.2:latest

# Create config
mkdir -p ~/.crew-ai
cat > ~/.crew-ai/config.yaml << EOF
llm:
  environment: "local"
  ollama_model: "llama3.2:latest"
EOF
chmod 600 ~/.crew-ai/config.yaml
```

**See:** [Complete Ollama Guide](ollama.md) | [Configuration Guide](configuration.md)

## Your First Project

### Option 1: Using Make

```bash
make run-workflow VISION="Create a simple Python calculator with add and subtract functions"
```

### Option 2: Using Web UI

```bash
# Start web server
make run-web

# Open browser
open http://localhost:8080
```

Then enter your vision in the web interface and click "Start Build".

### Option 3: Using Python

```python
from llamaindex_crew.main import run_workflow

results = run_workflow(
    vision="Create a simple Python calculator",
    project_id="my-calculator"
)

print(f"Status: {results['status']}")
print(f"Cost: ${results['budget_report']['total_cost']:.4f}")
```

## What Happens Next?

The system will:

1. **🧠 Meta Agent** - Generates custom agent backstories
2. **📋 Product Owner** - Creates user stories and requirements
3. **🎨 Designer** - Designs the system architecture
4. **🏗️ Tech Architect** - Defines tech stack and file structure
5. **💻 Development** - Implements features with TDD
6. **🖼️ Frontend** - Creates UI components
7. **✅ Completion** - Validates and delivers code

## Expected Output

Your generated code will be in:

```
workspace/
└── my-calculator/
    ├── requirements.md
    ├── user_stories.md
    ├── design_spec.md
    ├── tech_stack.md
    └── src/
        ├── calculator/
        │   ├── __init__.py
        │   ├── calculator.py
        │   └── exceptions.py
        └── tests/
            └── test_calculator.py
```

## Running Tests

```bash
# Quick tests (< 1 minute)
make test-quick

# With coverage
make test-coverage

# E2E tests (requires API key)
make test-e2e
```

## Next Steps

- 📖 [Read the User Guide](../guide/overview.md)
- 🧪 [Learn about Testing](../testing/overview.md)
- 🏗️ [Understand the Architecture](../architecture/design.md)
- 🐳 [Deploy with Docker](../deployment/docker.md)

## Troubleshooting

### API Key Not Found

```bash
# Check environment
make check-env

# Ensure .env file is loaded
cat .env | grep API_KEY
```

### Import Errors

```bash
# Ensure package is installed
pip install -e .

# Or use PYTHONPATH
export PYTHONPATH=$(pwd)/src
```

### Budget Exceeded

```bash
# Increase budget limits in .env
BUDGET_MAX_COST_PER_PROJECT=20.0
BUDGET_MAX_COST_PER_HOUR=10.0
```

## Getting Help

- 📚 [Documentation](../guide/overview.md)
- 💬 [GitHub Discussions](https://github.com/varkrish/varkrish-crewai-opl-coder/discussions)
- 🐛 [Report Issues](https://github.com/varkrish/varkrish-crewai-opl-coder/issues)
- 📧 [Contact](mailto:support@example.com)

---

**Ready to build something amazing? Let's go! 🚀**
