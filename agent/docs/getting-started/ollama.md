# Using Ollama for Local Development

[Ollama](https://ollama.com) enables you to run large language models locally on your machine. This is perfect for:

- 🆓 **Free development** - No API costs
- 🔒 **Privacy** - All data stays on your machine
- ⚡ **Fast iteration** - No network latency
- 🌐 **Offline work** - No internet required
- 🧪 **Testing** - Safe environment for experimentation

## Installation

### macOS

```bash
# Using Homebrew
brew install ollama

# Or download from https://ollama.com
```

### Linux

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

### Windows

Download the installer from [https://ollama.com/download/windows](https://ollama.com/download/windows)

### Docker

```bash
docker run -d -v ollama:/root/.ollama -p 11434:11434 --name ollama ollama/ollama
```

## Quick Start

### 1. Start Ollama

```bash
# Start the Ollama service
ollama serve
```

The service will run on `http://localhost:11434` by default.

### 2. Pull a Model

```bash
# Recommended: Fast and efficient
ollama pull llama3.2:latest

# Or other models:
ollama pull codellama         # Code-focused model
ollama pull mistral          # Fast alternative
ollama pull deepseek-coder   # Code generation specialist
ollama pull llama3:70b       # More powerful (requires more RAM)
```

### 3. Configure AI Crew

Create a config file for Ollama:

```bash
# Create config directory
mkdir -p ~/.crew-ai

# Create config file
cat > ~/.crew-ai/config.yaml << EOF
llm:
  environment: "local"
  ollama_base_url: "http://localhost:11434"
  ollama_model: "llama3.2:latest"
  temperature: 0.7

budget:
  max_cost_per_project: 0.0  # Free!
  max_cost_per_hour: 0.0

workspace:
  path: "./workspace"

logging:
  level: "INFO"
EOF

# Set secure permissions
chmod 600 ~/.crew-ai/config.yaml
```

### 4. Run Your First Project

```bash
# Using config file
python -m llamaindex_crew.main "Create a simple calculator app"

# The system will automatically use Ollama!
```

## Configuration Options

### Config File (Recommended)

```yaml
# ~/.crew-ai/config.yaml
llm:
  # Set to "local" to use Ollama
  environment: "local"
  
  # Ollama server URL
  ollama_base_url: "http://localhost:11434"
  
  # Model to use
  ollama_model: "llama3.2:latest"
  
  # Model parameters
  temperature: 0.7
  max_tokens: 2048
```

### Environment Variables (Alternative)

```bash
export LLM_ENVIRONMENT=local
export OLLAMA_BASE_URL=http://localhost:11434
export OLLAMA_MODEL=llama3.2:latest
```

### Docker Compose

```yaml
version: '3.8'

services:
  ollama:
    image: ollama/ollama
    ports:
      - "11434:11434"
    volumes:
      - ollama_data:/root/.ollama
    
  crew-ai:
    build: .
    depends_on:
      - ollama
    environment:
      - LLM_ENVIRONMENT=local
      - OLLAMA_BASE_URL=http://ollama:11434
      - OLLAMA_MODEL=llama3.2:latest
    volumes:
      - ./workspace:/app/workspace

volumes:
  ollama_data:
```

## Recommended Models

### For Development

| Model | Size | RAM Required | Speed | Quality | Best For |
|-------|------|--------------|-------|---------|----------|
| `llama3.2:latest` | 3.2GB | 8GB | ⚡⚡⚡ | ⭐⭐⭐ | General development |
| `mistral:latest` | 4.1GB | 8GB | ⚡⚡⚡ | ⭐⭐⭐ | Fast iteration |
| `codellama:latest` | 3.8GB | 8GB | ⚡⚡ | ⭐⭐⭐⭐ | Code generation |

### For Production-Quality Code

| Model | Size | RAM Required | Speed | Quality | Best For |
|-------|------|--------------|-------|---------|----------|
| `deepseek-coder:latest` | 16GB | 32GB | ⚡ | ⭐⭐⭐⭐⭐ | Advanced code gen |
| `llama3:70b` | 40GB | 64GB | ⚡ | ⭐⭐⭐⭐⭐ | Complex projects |
| `qwen2.5-coder:latest` | 14GB | 32GB | ⚡⚡ | ⭐⭐⭐⭐ | Code with reasoning |

### Checking Model Info

```bash
# List available models
ollama list

# Show model details
ollama show llama3.2:latest

# Pull specific version
ollama pull llama3.2:3b
```

## Performance Tuning

### System Requirements

**Minimum:**
- 8GB RAM
- 10GB disk space
- CPU: 4 cores

**Recommended:**
- 16GB+ RAM
- 50GB disk space
- CPU: 8+ cores or Apple M1/M2/M3

**Optimal:**
- 32GB+ RAM
- 100GB SSD
- GPU: NVIDIA RTX 3060+ (12GB VRAM)

### GPU Acceleration

#### NVIDIA GPU (Linux/Windows)

```bash
# Install NVIDIA Container Toolkit
# https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html

# Run with GPU
docker run -d --gpus=all -v ollama:/root/.ollama -p 11434:11434 ollama/ollama
```

#### Apple Silicon (M1/M2/M3)

Ollama automatically uses Metal acceleration on macOS - no additional setup needed!

### Optimize for Speed

```yaml
# ~/.crew-ai/config.yaml
llm:
  environment: "local"
  ollama_model: "llama3.2:latest"
  temperature: 0.7
  max_tokens: 1024  # Lower for faster responses
```

## Switching Between Ollama and Cloud

### Development → Production

```bash
# Development (Ollama)
cat > ~/.crew-ai/config-dev.yaml << EOF
llm:
  environment: "local"
  ollama_model: "llama3.2:latest"
EOF

# Production (Red Hat MaaS)
cat > ~/.crew-ai/config-prod.yaml << EOF
llm:
  environment: "production"
  api_key: "your_production_key"
  api_base_url: "https://litellm-prod.apps.maas.redhatworkshops.io"
  model_worker: "gpt-4o-mini"
EOF

# Use different configs
python -m llamaindex_crew.main --config ~/.crew-ai/config-dev.yaml "test locally"
python -m llamaindex_crew.main --config ~/.crew-ai/config-prod.yaml "deploy to prod"
```

### Web UI Configuration

The web UI automatically detects your configuration:

```bash
# Start web server with Ollama config
export CONFIG_FILE_PATH=~/.crew-ai/config-dev.yaml
python -m llamaindex_crew.web.web_app

# Or for production
export CONFIG_FILE_PATH=~/.crew-ai/config-prod.yaml
python -m llamaindex_crew.web.web_app
```

## Troubleshooting

### Ollama Not Running

```bash
# Check if Ollama is running
curl http://localhost:11434/api/tags

# Start Ollama
ollama serve

# Check Ollama version
ollama --version
```

### Model Not Found

```bash
# List available models
ollama list

# Pull the model
ollama pull llama3.2:latest

# Verify it's available
ollama run llama3.2:latest "Hello"
```

### Connection Refused

```bash
# Check Ollama service
ps aux | grep ollama

# Check port is open
lsof -i :11434

# Restart Ollama
killall ollama
ollama serve
```

### Out of Memory

```bash
# Use a smaller model
ollama pull llama3.2:3b  # 3B parameters (smaller)

# Or close other applications
# Or increase RAM/swap

# Check memory usage
ollama ps
```

### Slow Generation

**Options:**
1. Use a smaller model (`llama3.2:3b` instead of `:latest`)
2. Reduce `max_tokens` in config
3. Enable GPU acceleration
4. Close background applications
5. Use SSD instead of HDD

### Wrong Model Loaded

```bash
# Check what's running
ollama ps

# Stop all models
ollama stop --all

# Verify config
python -m llamaindex_crew.main --show-config
```

## Health Checks with Ollama

Test your Ollama setup:

```bash
# Start web server
python -m llamaindex_crew.web.web_app

# Check readiness (should be healthy)
curl http://localhost:8080/health/ready | jq

# Deep LLM check (tests actual Ollama connection)
curl http://localhost:8080/health/llm | jq
```

Expected response:

```json
{
  "status": "healthy",
  "checks": {
    "config": {
      "status": "healthy",
      "llm_environment": "local"
    },
    "llm_connectivity": {
      "status": "healthy",
      "message": "LLM responded successfully",
      "response_time_seconds": 0.234
    }
  }
}
```

## Best Practices

### 1. Start Small

Begin with `llama3.2:latest` for development, then upgrade to larger models as needed.

### 2. Keep Models Updated

```bash
# Update all models
ollama list | tail -n +2 | awk '{print $1}' | xargs -I {} ollama pull {}
```

### 3. Monitor Resources

```bash
# Watch Ollama resource usage
watch -n 1 "ollama ps"

# Or with top
top -p $(pgrep ollama)
```

### 4. Use Appropriate Models

- **Quick tests**: `llama3.2:3b`
- **Development**: `llama3.2:latest` or `codellama`
- **Production testing**: `deepseek-coder` or `llama3:70b`

### 5. Clean Up Old Models

```bash
# Remove unused models
ollama rm old-model:tag

# Free up space
ollama prune
```

## Comparison: Ollama vs Cloud

| Feature | Ollama (Local) | Cloud APIs |
|---------|---------------|------------|
| **Cost** | Free | $0.01 - $0.10 per request |
| **Privacy** | 100% local | Data sent to provider |
| **Speed** | Fast (no network) | Varies (network latency) |
| **Quality** | Good | Excellent |
| **Offline** | ✅ Yes | ❌ No |
| **Setup** | Moderate | Easy |
| **Scalability** | Limited by hardware | Unlimited |
| **Models** | Open source | Proprietary + open |

## Example Workflows

### Test Locally, Deploy with Cloud

```bash
# 1. Develop and test with Ollama (free)
python -m llamaindex_crew.main \
  --config config-ollama.yaml \
  "Create a REST API with FastAPI"

# 2. Review generated code

# 3. Deploy final version with cloud API
python -m llamaindex_crew.main \
  --config config-production.yaml \
  "Create a REST API with FastAPI"
```

### CI/CD with Ollama

```yaml
# .github/workflows/test.yml
name: Test AI Crew

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    
    services:
      ollama:
        image: ollama/ollama
        ports:
          - 11434:11434
    
    steps:
      - uses: actions/checkout@v3
      
      - name: Pull model
        run: |
          docker exec ollama ollama pull llama3.2:latest
      
      - name: Run tests
        env:
          LLM_ENVIRONMENT: local
          OLLAMA_BASE_URL: http://localhost:11434
          OLLAMA_MODEL: llama3.2:latest
        run: |
          make test-e2e
```

## Next Steps

- 📖 [Complete Configuration Guide](configuration.md)
- 🔒 [Secure Configuration Patterns](../deployment/secure-config-patterns.md)
- 🏗️ [System Architecture](../architecture/design.md)
- 🧪 [Testing Guide](../testing/overview.md)

## Resources

- 🌐 [Ollama Official Site](https://ollama.com)
- 📚 [Ollama Documentation](https://github.com/ollama/ollama/tree/main/docs)
- 🤖 [Available Models](https://ollama.com/library)
- 💬 [Ollama Discord](https://discord.gg/ollama)

---

**Ready to develop for free with Ollama? 🚀**
