# AI Software Development Crew

<div align="center">

![Red Hat Inspired](https://img.shields.io/badge/Style-Red%20Hat%20Inspired-EE0000?style=for-the-badge&logo=redhat)
![Python](https://img.shields.io/badge/python-3.10%2B-EE0000?style=for-the-badge&logo=python&logoColor=white)
![LlamaIndex](https://img.shields.io/badge/LlamaIndex-0.10%2B-EE0000?style=for-the-badge)
![Tests](https://img.shields.io/badge/tests-57%20passing-success?style=for-the-badge)

**A scalable, multi-agent AI software development system powered by LlamaIndex**

[Quick Start](getting-started/quickstart.md){ .md-button .md-button--primary }
[Documentation](guide/overview.md){ .md-button }
[GitHub](https://github.com/varkrish/varkrish-crewai-opl-coder){ .md-button }

</div>

---

!!! tip "Red Hat Inspired Design"
    This documentation uses Red Hat's design principles and color palette for a professional, enterprise-ready look.

## Overview

AI Software Development Crew is a comprehensive multi-agent system that automates the entire software development lifecycle, from vision to deployable code. Built with **LlamaIndex**, it provides a cost-efficient, scalable solution for code generation with budget tracking and quality assurance.

### ✨ Key Features

<div class="grid cards" markdown>

-   :material-robot:{ .lg .middle } __Multi-Agent System__

    ---

    Specialized agents for each development phase (Meta, Product Owner, Designer, Tech Architect, Dev, Frontend)

-   :material-power-plug:{ .lg .middle } __Pluggable Architecture__

    ---

    Works with any OpenAI-compatible API: vLLM, Ollama, LocalAI, LiteLLM, cloud providers

-   :material-cash:{ .lg .middle } __Budget Tracking__

    ---

    Real-time cost monitoring with configurable limits and 20x cost reduction using OpenRouter

-   :material-test-tube:{ .lg .middle } __TDD Workflow__

    ---

    Test-Driven Development enforced with comprehensive test coverage

-   :material-file-document:{ .lg .middle } __BDD Requirements__

    ---

    Gherkin scenarios for all features with clear acceptance criteria

-   :material-tools:{ .lg .middle } __Custom Tools__

    ---

    File operations, Git integration, and test runners

-   :material-web:{ .lg .middle } __Web UI__

    ---

    Beautiful Flask-based interface for job management

</div>

### 🚀 Quick Example

**Option 1: Free with Ollama (Development)**

```bash
# Install Ollama
brew install ollama && ollama serve
ollama pull llama3.2:latest

# Create config
mkdir -p ~/.crew-ai
cat > ~/.crew-ai/config.yaml << EOF
llm:
  environment: "local"
  ollama_model: "llama3.2:latest"
EOF

# Run - 100% FREE!
python -m llamaindex_crew.main "Create a calculator"
```

**Option 2: Cloud API (Production)**

```bash
# Create secure config
mkdir -p ~/.crew-ai
cp config.example.yaml ~/.crew-ai/config.yaml
chmod 600 ~/.crew-ai/config.yaml
# Edit with your API key

# Run
python -m llamaindex_crew.main "Create a calculator"
```

**Learn more:** [Quick Start](getting-started/quickstart.md) | [Using Ollama](getting-started/ollama.md)

### 📈 Workflow

```mermaid
graph LR
    A[Vision] --> B[Meta Agent]
    B --> C[Product Owner]
    C --> D[Designer]
    D --> E[Tech Architect]
    E --> F[Development]
    F --> G[Frontend]
    G --> H[Completed]
    
    style A fill:#EE0000,stroke:#A30000,color:#FFF
    style H fill:#3E8635,stroke:#2E6629,color:#FFF
    style B fill:#0066CC,stroke:#004D99,color:#FFF
    style C fill:#0066CC,stroke:#004D99,color:#FFF
    style D fill:#0066CC,stroke:#004D99,color:#FFF
    style E fill:#0066CC,stroke:#004D99,color:#FFF
    style F fill:#0066CC,stroke:#004D99,color:#FFF
    style G fill:#0066CC,stroke:#004D99,color:#FFF
```

### 🎯 Use Cases

=== "Code Generation"
    Generate complete applications from natural language descriptions
    
    ```bash
    make run-workflow VISION="Build a REST API for managing books"
    ```

=== "Testing"
    Comprehensive test suite with E2E, API, and UI tests
    
    ```bash
    make test-quick  # Fast tests
    make test-e2e    # Full E2E validation
    ```

=== "Web UI"
    Monitor and manage projects through the web interface
    
    ```bash
    make run-web
    # Open http://localhost:8080
    ```

### 📊 Architecture Highlights

| Component | Description | Technology |
|-----------|-------------|------------|
| **Agents** | Meta, Product Owner, Designer, Tech Architect, Dev, Frontend | LlamaIndex ReActAgent |
| **State Machine** | Workflow orchestration | Custom FSM |
| **Task Manager** | Task tracking & validation | SQLite |
| **Budget Tracker** | Cost monitoring | Token counting |
| **Tools** | File, Git, Test operations | Custom LlamaIndex tools |
| **Web UI** | Job management | Flask + REST API |

### 🎉 What's New in LlamaIndex Migration

!!! success "Migration Complete"
    Successfully migrated from CrewAI to LlamaIndex with significant improvements:
    
    - ✅ **Pluggable LLM architecture** - any OpenAI-compatible API
    - ✅ **Secure config files** with permission validation & encryption
    - ✅ **Free local development** - Ollama, vLLM, LocalAI, llama.cpp
    - ✅ **Production ready** - Cloud APIs or self-hosted vLLM
    - ✅ **Health check endpoints** for production monitoring
    - ✅ **Docker/Kubernetes secrets** integration
    - ✅ **20x cost reduction** with OpenRouter
    - ✅ **Comprehensive testing** with 57 passing tests
    - ✅ **GitHub Pages docs** with Red Hat theme
    - ✅ **Docker/Kubernetes secrets** integration
    - ✅ **Enhanced budget control** with max iterations
    - ✅ **Comprehensive E2E tests** (57 tests across 5 categories)
    - ✅ **RAG support** with document indexing
    - ✅ **Budget control** with max iterations

### 📚 Documentation

<div class="grid cards" markdown>

-   :material-clock-fast:{ .lg .middle } __Quick Start__

    ---

    Get up and running in 5 minutes

    [:octicons-arrow-right-24: Getting started](getting-started/quickstart.md)

-   :material-book-open-variant:{ .lg .middle } __User Guide__

    ---

    Learn how to use the system

    [:octicons-arrow-right-24: Read the guide](guide/overview.md)

-   :material-test-tube:{ .lg .middle } __Testing__

    ---

    Comprehensive testing documentation

    [:octicons-arrow-right-24: Testing guide](testing/overview.md)

-   :material-api:{ .lg .middle } __API Reference__

    ---

    Detailed API documentation

    [:octicons-arrow-right-24: API docs](api/overview.md)

</div>

### 💻 Development with Makefile

```bash
# Install dependencies
make install-dev

# Run tests
make test-quick      # Fast tests (< 1 min)
make test-coverage   # With coverage report

# Format code
make format

# Build docs
make docs-serve      # Local preview
make docs-deploy     # Deploy to GitHub Pages

# Run application
make run-web         # Start web UI
```

### 🤝 Contributing

We welcome contributions! See our [Contributing Guide](development/contributing.md) for details.

### 📄 License

This project is licensed under the MIT License - see the [LICENSE](license.md) file for details.

### 🔗 Links

- [GitHub Repository](https://github.com/varkrish/varkrish-crewai-opl-coder)
- [Issue Tracker](https://github.com/varkrish/varkrish-crewai-opl-coder/issues)
- [Discussions](https://github.com/varkrish/varkrish-crewai-opl-coder/discussions)

---

<div align="center" class="rh-banner">

**Built with ❤️ using [LlamaIndex](https://www.llamaindex.ai/)**

Styled with Red Hat design principles for enterprise excellence

</div>
