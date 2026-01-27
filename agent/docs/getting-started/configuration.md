# LLM Configuration Guide

## Quick Start

The AI Software Development Crew uses a **simple, generic configuration** that works with **any OpenAI-compatible LLM provider**.

### Required Configuration

```bash
# .env file
LLM_API_KEY=your_api_key
LLM_API_BASE_URL=https://your-provider.com/v1  # Optional
```

That's it! Two variables to configure any provider.

## Deployment Scenarios

### Local Development

```bash
# User config (auto-detected)
~/.crew-ai/config.yaml (chmod 600)
```

### Docker

```bash
# Option 1: Docker secrets (recommended)
docker secret create crew_config config.yaml
docker service create --secret crew_config crew-ai

# Option 2: Volume mount
docker run -v ~/.crew-ai/config.yaml:/app/config.yaml:ro \
           -e CONFIG_FILE_PATH=/app/config.yaml \
           crew-ai
```

### Kubernetes/OpenShift

```bash
# Create secret
kubectl create secret generic crew-ai-config \
  --from-file=config.yaml

# Mount in deployment
volumeMounts:
  - name: config
    mountPath: /var/secrets
    readOnly: true
```

For comprehensive deployment patterns, see [Secure Config Patterns](../deployment/secure-config-patterns.md).

## CLI Usage

### Show Current Configuration

```bash
python -m llamaindex_crew.main --show-config
```

### Specify Config File

```bash
python -m llamaindex_crew.main \
  --config /path/to/config.yaml \
  "Create a calculator"
```

### Use Encrypted Config

```bash
python -m llamaindex_crew.main \
  --config config.encrypted.yaml \
  --encryption-key "wT6DPz..." \
  "Create a calculator"
```

## Supported Providers (Examples)

### Red Hat MaaS (Recommended)

Enterprise LLM platform with Red Hat support.

```bash
LLM_API_KEY=your_maas_key
LLM_API_BASE_URL=https://litellm-prod.apps.maas.redhatworkshops.io
LLM_ENVIRONMENT=production
```

### OpenRouter

Cost-effective access to multiple LLM providers.

```bash
LLM_API_KEY=sk-or-v1-...
LLM_API_BASE_URL=https://openrouter.ai/api/v1
LLM_ENVIRONMENT=production
```

### Azure OpenAI

Microsoft Azure hosted OpenAI models.

```bash
LLM_API_KEY=your_azure_key
LLM_API_BASE_URL=https://YOUR_RESOURCE.openai.azure.com
LLM_ENVIRONMENT=production
```

### Standard OpenAI

Direct OpenAI API access.

```bash
LLM_API_KEY=sk-...
# LLM_API_BASE_URL not needed - uses default
LLM_ENVIRONMENT=production
```

### Local Development (Ollama)

Free, offline development with local models.

```bash
LLM_ENVIRONMENT=local
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2:latest
# No API key required!
```

## Configuration Options

### Core Settings

| Variable | Required | Description | Default |
|----------|----------|-------------|---------|
| `LLM_API_KEY` | Yes (prod) | API key for your provider | - |
| `LLM_API_BASE_URL` | No | Base URL for API endpoint | OpenAI default |
| `LLM_ENVIRONMENT` | No | `production` or `local` | `production` |

### Model Selection

| Variable | Description | Default |
|----------|-------------|---------|
| `LLM_MODEL_MANAGER` | Model for orchestration | `gpt-4o-mini` |
| `LLM_MODEL_WORKER` | Model for code generation | `gpt-4o-mini` |
| `LLM_MODEL_REVIEWER` | Model for code review | `gpt-4o-mini` |

### Advanced Settings

| Variable | Description | Default |
|----------|-------------|---------|
| `LLM_MAX_TOKENS` | Maximum tokens per request | `2048` |
| `LLM_TEMPERATURE` | Sampling temperature | `0.7` |
| `LLM_EMBEDDING_MODEL` | Embedding model name | `text-embedding-3-small` |

### Local Development

| Variable | Description | Default |
|----------|-------------|---------|
| `OLLAMA_BASE_URL` | Ollama server URL | `http://localhost:11434` |
| `OLLAMA_MODEL` | Ollama model name | `llama3.2:latest` |

## Usage Examples

### Using Environment Variables

```bash
export LLM_API_KEY=your_key
export LLM_API_BASE_URL=https://your-provider.com/v1
make run-workflow VISION="Create a calculator"
```

### Using .env File

```bash
# Create .env from example
cp .env.example .env

# Edit .env with your settings
LLM_API_KEY=your_key
LLM_API_BASE_URL=https://your-provider.com/v1

# Run
make run-workflow VISION="Create a calculator"
```

### In Python

```python
import os
from llamaindex_crew.main import run_workflow

# Configure
os.environ["LLM_API_KEY"] = "your_key"
os.environ["LLM_API_BASE_URL"] = "https://your-provider.com/v1"

# Run
results = run_workflow(
    vision="Create a simple calculator",
    project_id="calculator"
)
```

### Web UI

```bash
# Configure in .env
echo "LLM_API_KEY=your_key" >> .env
echo "LLM_API_BASE_URL=https://your-provider.com/v1" >> .env

# Start UI
make run-web
open http://localhost:8080
```

## Verification

### Check Configuration

```bash
python -c "from llamaindex_crew.utils.llm_config import print_llm_config; print_llm_config()"
```

Expected output:
```
======================================================================
🔧 LLM Configuration
======================================================================
Environment: production

Provider: Red Hat MaaS
  API Key: ✓ Set
  Base URL: https://litellm-prod.apps.maas.redhatworkshops.io

Models:
  Manager: gpt-4o-mini
  Worker: gpt-4o-mini
  Reviewer: gpt-4o-mini

Parameters:
  Max Tokens: 2048
  Temperature: 0.7

📊 Embeddings: OpenAI-compatible
   Model: text-embedding-3-small
======================================================================
```

### Test Connection

```python
from llamaindex_crew.utils.llm_config import get_llm_for_agent

llm = get_llm_for_agent("worker")
response = llm.complete("Hello!")
print(response.text)
```

## Provider Switching

Switch between providers by changing just 2 variables:

### From OpenAI to Red Hat MaaS

```bash
# Before
LLM_API_KEY=sk-...
# (no base URL)

# After
LLM_API_KEY=maas_key
LLM_API_BASE_URL=https://litellm-prod.apps.maas.redhatworkshops.io
```

### From Production to Local

```bash
# Before
LLM_ENVIRONMENT=production
LLM_API_KEY=your_key

# After
LLM_ENVIRONMENT=local
# No API key needed!
```

## Troubleshooting

### Error: "LLM_API_KEY environment variable is required"

Set your API key:
```bash
export LLM_API_KEY=your_key
# or add to .env
echo "LLM_API_KEY=your_key" >> .env
```

### Error: "Connection refused"

Check your base URL:
```bash
echo $LLM_API_BASE_URL
curl $LLM_API_BASE_URL/models \
  -H "Authorization: Bearer $LLM_API_KEY"
```

### Error: "Invalid model"

Update model names for your provider:
```bash
export LLM_MODEL_WORKER=valid-model-name
```

### Wrong provider detected

The system auto-detects providers from the URL. Ensure your base URL is correct:
```bash
# Red Hat MaaS: contains "maas" or "redhat"
# OpenRouter: contains "openrouter"
# Azure: contains "azure"
# Local: contains "localhost" or "127.0.0.1"
```

## Best Practices

### Development

Use local Ollama for free development:
```bash
export LLM_ENVIRONMENT=local
ollama serve
```

### Testing

Use cost limits:
```bash
export LLM_API_KEY=your_key
export BUDGET_MAX_COST_PER_PROJECT=10.0
make test-e2e
```

### Production

Use Red Hat MaaS or trusted provider:
```bash
export LLM_API_KEY=production_key
export LLM_API_BASE_URL=https://enterprise-provider.com/v1
export LLM_ENVIRONMENT=production
```

### Cost Optimization

- Use `gpt-4o-mini` for most tasks
- Set `LLM_MAX_TOKENS=1024` to reduce costs
- Configure budget limits:
  ```bash
  BUDGET_MAX_COST_PER_PROJECT=100.0
  BUDGET_MAX_COST_PER_HOUR=10.0
  ```

## Benefits

✅ **Simple**: Only 2 required variables  
✅ **Flexible**: Any OpenAI-compatible provider  
✅ **Generic**: No provider-specific code  
✅ **Future-proof**: New providers work automatically  
✅ **Portable**: Easy to switch providers  
✅ **Testable**: Local Ollama for development  

## See Also

- [Quick Start Guide](../getting-started/quickstart.md)
- [Budget Tracking](budget.md)
- [Testing Guide](../testing/overview.md)
- [API Reference](../api/overview.md)
