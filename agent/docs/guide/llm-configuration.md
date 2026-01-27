# LLM Configuration Guide

## Pluggable Architecture

The system uses a **pluggable architecture** that works with **any OpenAI-compatible API**. This means:

- ✅ No provider-specific code
- ✅ Just configure the API endpoint and model
- ✅ Works with vLLM, Ollama, LocalAI, LiteLLM, and any OpenAI-compatible service
- ✅ Easy to switch between providers

**Philosophy:** *"If you speak OpenAI API, you're welcome!"*

## Configuration Examples

### Cloud Providers

#### Option 1: Red Hat MaaS (Enterprise)

**Config file:**
```yaml
llm:
  environment: "production"
  api_key: "your_redhat_maas_key"
  api_base_url: "https://litellm-prod.apps.maas.redhatworkshops.io"
  model_manager: "gpt-4o-mini"
  model_worker: "gpt-4o-mini"
  model_reviewer: "gpt-4o-mini"
```

#### Option 2: OpenRouter (Multi-Model Access)

**Config file:**
```yaml
llm:
  environment: "production"
  api_key: "sk-or-v1-..."
  api_base_url: "https://openrouter.ai/api/v1"
  model_manager: "anthropic/claude-3.5-sonnet"
  model_worker: "qwen/qwen3-coder-plus"
```

#### Option 3: Standard OpenAI

**Config file:**
```yaml
llm:
  environment: "production"
  api_key: "sk-..."
  # api_base_url not needed - uses default
  model_worker: "gpt-4o-mini"
```

#### Option 4: Anthropic Claude

**Config file:**
```yaml
llm:
  environment: "production"
  api_key: "sk-ant-..."
  api_base_url: "https://api.anthropic.com/v1"
  model_worker: "claude-3-opus"
```

### Local & Self-Hosted Options

#### Option 5: Ollama (Easy Local Development)

Ollama provides the easiest way to run models locally.

**Config file:**
```yaml
llm:
  environment: "local"  # Special mode for Ollama
  ollama_base_url: "http://localhost:11434"
  ollama_model: "llama3.2:latest"
```

**Start Ollama:**
```bash
ollama serve
ollama pull llama3.2:latest
```

**See:** [Complete Ollama Guide](../getting-started/ollama.md)

#### Option 6: vLLM (High-Performance Inference)

vLLM provides production-grade inference with high throughput and advanced features.

**Config file:**
```yaml
llm:
  environment: "production"
  api_key: "dummy"  # vLLM doesn't validate
  api_base_url: "http://localhost:8000/v1"
  model_worker: "meta-llama/Llama-3.1-8B"
  model_manager: "meta-llama/Llama-3.1-8B"
  model_reviewer: "meta-llama/Llama-3.1-8B"
  temperature: 0.7
```

**Start vLLM server:**
```bash
# Install vLLM
pip install vllm

# Start OpenAI-compatible server
python -m vllm.entrypoints.openai.api_server \
  --model meta-llama/Llama-3.1-8B \
  --port 8000 \
  --tensor-parallel-size 2  # For multi-GPU
```

**Advanced options:**
```bash
# With quantization for lower memory
python -m vllm.entrypoints.openai.api_server \
  --model meta-llama/Llama-3.1-70B \
  --quantization awq \
  --dtype half

# With specific GPU memory utilization
python -m vllm.entrypoints.openai.api_server \
  --model meta-llama/Llama-3.1-8B \
  --gpu-memory-utilization 0.9
```

**When to use vLLM:**
- ✅ Production deployments requiring high throughput
- ✅ Batch processing of multiple requests
- ✅ Multi-GPU inference
- ✅ Need for advanced features (continuous batching, PagedAttention)
- ✅ Cost optimization for large-scale inference

#### Option 7: LocalAI (Multi-Model Local Server)

LocalAI is a drop-in OpenAI replacement that runs locally.

**Config file:**
```yaml
llm:
  environment: "production"
  api_key: "dummy"
  api_base_url: "http://localhost:8080/v1"
  model_worker: "gpt-3.5-turbo"  # LocalAI model alias
```

**Start LocalAI:**
```bash
# Using Docker
docker run -p 8080:8080 \
  -v $PWD/models:/models \
  localai/localai:latest

# Or install locally
curl https://localai.io/install.sh | sh
local-ai
```

#### Option 8: LiteLLM Proxy (Universal Gateway)

LiteLLM provides a unified interface to 100+ LLM providers.

**Config file:**
```yaml
llm:
  environment: "production"
  api_key: "your-litellm-key"
  api_base_url: "http://localhost:4000"
  model_worker: "gpt-4"  # Routes to configured backend
```

**Start LiteLLM proxy:**
```bash
# Install
pip install litellm[proxy]

# Configure backends in config.yaml
cat > litellm_config.yaml << EOF
model_list:
  - model_name: gpt-4
    litellm_params:
      model: ollama/llama3.2
      api_base: http://localhost:11434
  - model_name: claude
    litellm_params:
      model: anthropic/claude-3-sonnet
      api_key: your-key
EOF

# Start proxy
litellm --config litellm_config.yaml --port 4000
```

**Benefits:**
- Route to different providers transparently
- Load balancing across providers
- Fallback mechanisms
- Cost tracking and budgets
- Rate limiting

#### Option 9: Text Generation WebUI

Oobabooga's Text Generation WebUI with OpenAI extension.

**Config file:**
```yaml
llm:
  environment: "production"
  api_key: "dummy"
  api_base_url: "http://localhost:5000/v1"
  model_worker: "your-model-name"
```

**Start WebUI with OpenAI extension:**
```bash
python server.py --api --extensions openai
```

#### Option 10: llama.cpp Server

Lightweight C++ implementation for running LLMs.

**Config file:**
```yaml
llm:
  environment: "production"
  api_key: "dummy"
  api_base_url: "http://localhost:8080/v1"
  model_worker: "llama-model"
```

**Start llama.cpp server:**
```bash
./server -m models/llama-2-7b.gguf --port 8080
```

## Provider Comparison

| Provider | Type | Best For | Cost | Setup | Performance |
|----------|------|----------|------|-------|-------------|
| **Red Hat MaaS** | Cloud | Enterprise production | Paid | Easy | Excellent |
| **OpenRouter** | Cloud | Multi-model access | Pay-per-use | Easy | Excellent |
| **OpenAI** | Cloud | Latest models | Paid | Easy | Excellent |
| **Anthropic** | Cloud | Reasoning tasks | Paid | Easy | Excellent |
| **Ollama** | Local | Dev, ease-of-use | Free | Very Easy | Good |
| **vLLM** | Self-hosted | Production inference | Free* | Medium | Excellent |
| **LocalAI** | Local | Multi-model local | Free | Easy | Good |
| **LiteLLM Proxy** | Gateway | Multi-provider routing | Varies | Medium | Excellent |
| **llama.cpp** | Local | Resource-constrained | Free | Medium | Good |

*Infrastructure costs apply

## Usage Patterns

### Development → Production Pipeline

```bash
# 1. Develop locally with Ollama (free, fast iteration)
cat > ~/.crew-ai/config-dev.yaml << EOF
llm:
  environment: "local"
  ollama_model: "llama3.2:latest"
EOF

python -m llamaindex_crew.main --config config-dev.yaml "test feature"

# 2. Test with vLLM (production-like performance)
cat > ~/.crew-ai/config-staging.yaml << EOF
llm:
  environment: "production"
  api_key: "dummy"
  api_base_url: "http://vllm-server:8000/v1"
  model_worker: "meta-llama/Llama-3.1-8B"
EOF

python -m llamaindex_crew.main --config config-staging.yaml "test feature"

# 3. Deploy with cloud API (production)
cat > ~/.crew-ai/config-prod.yaml << EOF
llm:
  environment: "production"
  api_key: "encrypted:your-prod-key"
  api_base_url: "https://litellm-prod.apps.maas.redhatworkshops.io"
  model_worker: "gpt-4o-mini"
EOF

python -m llamaindex_crew.main --config config-prod.yaml "production run"
```

### Multi-Provider Load Balancing

Use LiteLLM proxy to balance across multiple providers:

```yaml
# LiteLLM config
model_list:
  - model_name: gpt-4
    litellm_params:
      model: openai/gpt-4
      api_key: key1
  - model_name: gpt-4
    litellm_params:
      model: anthropic/claude-3-opus
      api_key: key2
  - model_name: gpt-4
    litellm_params:
      model: openrouter/anthropic/claude-3-opus
      api_key: key3
```

### Hybrid Architecture

Combine multiple providers for different purposes:

```python
# Manager: Use powerful cloud model for planning
manager_config:
  api_base_url: "https://api.openai.com/v1"
  model_manager: "gpt-4"

# Workers: Use cost-effective self-hosted for implementation
worker_config:
  api_base_url: "http://vllm:8000/v1"
  model_worker: "meta-llama/Llama-3.1-70B"

# Reviewer: Use Anthropic for code review
reviewer_config:
  api_base_url: "https://api.anthropic.com/v1"
  model_reviewer: "claude-3-opus"
```

## Testing Your Configuration

### Health Check Endpoints

Test your LLM configuration using the health check endpoints:

```bash
# Start web server
python -m llamaindex_crew.web.web_app

# Check if LLM is accessible
curl http://localhost:8080/health/llm | jq
```

Expected response:
```json
{
  "status": "healthy",
  "checks": {
    "config": {
      "status": "healthy",
      "llm_environment": "production"
    },
    "llm_connectivity": {
      "status": "healthy",
      "message": "LLM responded successfully",
      "response_time_seconds": 0.847
    }
  }
}
```

### Manual Test

```bash
# Show current configuration
python -m llamaindex_crew.main --show-config

# Run a simple test
python -m llamaindex_crew.main "Create a hello world function"
```

## Troubleshooting

### Connection Refused

```bash
# Check if server is running
curl http://localhost:8000/v1/models  # For vLLM
curl http://localhost:11434/api/tags  # For Ollama

# Check health
curl http://localhost:8080/health/llm | jq
```

### Wrong Model

```bash
# Verify model name matches server
curl http://localhost:8000/v1/models | jq  # vLLM
ollama list  # Ollama

# Update config with correct model name
```

### Authentication Failed

```bash
# Check API key is set
python -m llamaindex_crew.main --show-config

# For self-hosted, use dummy key
api_key: "dummy"  # or "not-needed"
```

### Slow Performance

**For vLLM:**
```bash
# Increase GPU memory utilization
--gpu-memory-utilization 0.95

# Enable tensor parallelism for multi-GPU
--tensor-parallel-size 2

# Use quantization
--quantization awq
```

**For Ollama:**
```bash
# Use smaller model
ollama pull llama3.2:3b

# Check GPU is being used
ollama ps
```

## Best Practices

1. **Use config files** instead of environment variables for better security
2. **Set file permissions** to `chmod 600` on config files
3. **Use environment-specific configs** (dev, staging, prod)
4. **Test locally first** with Ollama or vLLM before deploying to production
5. **Monitor costs** with budget tracking
6. **Rotate API keys** regularly
7. **Use encryption** for sensitive values in shared environments
8. **Load balance** across providers for resilience
9. **Choose the right tool**:
   - **Ollama**: Quick local development
   - **vLLM**: Production self-hosted inference
   - **Cloud APIs**: Managed service, latest models
   - **LiteLLM**: Multi-provider orchestration

## Next Steps

- [Using Ollama](../getting-started/ollama.md) - Complete Ollama guide
- [Secure Configuration Patterns](../deployment/secure-config-patterns.md) - Production security
- [Health Checks](../deployment/health-checks.md) - Monitoring guide
- [Budget Tracking](budget.md) - Cost management
- [Quick Start Guide](../getting-started/quickstart.md) - Get started fast

## Resources

### Official Documentation
- [vLLM Documentation](https://docs.vllm.ai/)
- [Ollama Documentation](https://ollama.com/docs)
- [LocalAI Documentation](https://localai.io/docs/)
- [LiteLLM Documentation](https://docs.litellm.ai/)
- [llama.cpp Documentation](https://github.com/ggerganov/llama.cpp)

### Community
- [vLLM GitHub](https://github.com/vllm-project/vllm)
- [Ollama Discord](https://discord.gg/ollama)
- [LocalAI Discord](https://discord.gg/localai)

---

**The pluggable architecture means you're never locked in - switch providers anytime! 🔌✨**
