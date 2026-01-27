"""
Centralized LLM configuration for all agents
Supports local (Ollama) and production (OpenAI, Anthropic, Google, OpenRouter) environments
"""
import os
import logging
from typing import Literal
from crewai import LLM

logger = logging.getLogger(__name__)

AgentType = Literal["manager", "worker", "reviewer"]

# OpenRouter base URL
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def get_llm_for_agent(agent_type: AgentType = "worker") -> LLM:
    """
    Get configured LLM based on environment and agent type
    
    Args:
        agent_type: Type of agent (manager, worker, reviewer)
        
    Returns:
        Configured LLM instance
        
    Environment Variables:
        LLM_ENVIRONMENT: 'local' for Ollama, 'production' for cloud providers (TAKES PRECEDENCE)
        OPENROUTER_API_KEY: OpenRouter API key (if set, uses OpenRouter for all models in production)
        OLLAMA_BASE_URL: Ollama server URL (default: http://localhost:11434)
        OLLAMA_MODEL: Ollama model name (default: llama3.2:latest)
        LLM_MODEL_MANAGER: Model for manager agents (default: gpt-4o)
        LLM_MODEL_WORKER: Model for worker agents (default: gpt-4o-mini)
        LLM_MODEL_REVIEWER: Model for reviewer agents (default: gpt-4o-mini)
    """
    # LLM_ENVIRONMENT takes precedence over all other settings
    environment = os.getenv("LLM_ENVIRONMENT", "production").lower()
    
    if environment == "local":
        return _get_local_llm()
    else:
        return _get_production_llm(agent_type)


def _get_local_llm() -> LLM:
    """Configure Ollama for local development"""
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    model = os.getenv("OLLAMA_MODEL", "llama3.2:latest")
    
    logger.info(f"🏠 Using local Ollama: {model} at {base_url}")
    
    return LLM(
        model=f"ollama/{model}",
        base_url=base_url,
        temperature=0.7
    )


def _get_production_llm(agent_type: AgentType) -> LLM:
    """Configure cloud LLM for production"""
    # Check if OpenRouter is configured (takes precedence)
    openrouter_api_key = os.getenv("OPENROUTER_API_KEY")
    if openrouter_api_key:
        # When using OpenRouter, model selection is handled by OpenRouter
        # We use a simple default model format that LiteLLM recognizes
        # OpenRouter will route requests to available models automatically
        
        # Optional: Allow user to specify a model preference via env var
        # Format: "provider/model" (e.g., "openai/gpt-4o-mini", "x-ai/grok-beta")
        user_model = os.getenv(f"LLM_MODEL_{agent_type.upper()}", None)
        
        if user_model:
            logger.info(f"🌐 Using OpenRouter with model preference for {agent_type}: {user_model}")
            
            # Remove :free suffix if present (OpenRouter doesn't use this format)
            # Free models are identified differently on OpenRouter
            clean_model = user_model.replace(":free", "").strip()
            
            # Format: openrouter/provider/model for LiteLLM
            if "/" in clean_model and not clean_model.startswith("openrouter/"):
                final_model = f"openrouter/{clean_model}"
            elif clean_model.startswith("openrouter/"):
                final_model = clean_model
            else:
                # Simple model name - assume OpenAI format
                final_model = f"openrouter/openai/{clean_model}"
        else:
            # Default: Use a reliable, cheap model that OpenRouter supports
            # gpt-4o-mini is a good default (cheap, fast, reliable)
            logger.info(f"🌐 Using OpenRouter auto-routing for {agent_type} (default: gpt-4o-mini)")
            final_model = "openrouter/openai/gpt-4o-mini"
        
        logger.info(f"   Model format: {final_model}")
        
        return LLM(
            model=final_model,
            base_url=OPENROUTER_BASE_URL,
            api_key=openrouter_api_key,
            temperature=0.7
        )
    
    # Fall back to native providers (when OpenRouter is not configured)
    # User must specify models via env vars: LLM_MODEL_MANAGER, LLM_MODEL_WORKER, etc.
    model_map = {
        "manager": os.getenv("LLM_MODEL_MANAGER", "gpt-4o"),
        "worker": os.getenv("LLM_MODEL_WORKER", "gpt-4o-mini"),
        "reviewer": os.getenv("LLM_MODEL_REVIEWER", "gpt-4o-mini")
    }
    
    model = model_map.get(agent_type, "gpt-4o-mini")
    logger.info(f"☁️  Using native provider for {agent_type}: {model}")
    
    # Determine provider and configure accordingly
    if model.startswith("gpt"):
        return LLM(model=model, temperature=0.7)
    elif model.startswith("claude"):
        return LLM(model=model, temperature=0.7)
    elif model.startswith("gemini"):
        # Use native Google Gen AI provider
        google_api_key = os.getenv("GOOGLE_API_KEY")
        if not google_api_key:
            raise ValueError("GOOGLE_API_KEY environment variable is required for Gemini models")
        
        logger.info(f"🔑 Using Gemini native provider: {model}")
        
        return LLM(
            model=model,
            temperature=0.7,
            api_key=google_api_key
        )
    else:
        # Default to OpenAI format
        return LLM(model=model, temperature=0.7)


def get_available_models() -> dict:
    """
    Get information about available models
    
    Returns:
        Dictionary with model information
    """
    environment = os.getenv("LLM_ENVIRONMENT", "production").lower()
    
    if environment == "local":
        return {
            "environment": "local",
            "provider": "Ollama",
            "base_url": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            "model": os.getenv("OLLAMA_MODEL", "llama3.2:latest"),
            "cost": "Free (local)"
        }
    else:
        openrouter_api_key = os.getenv("OPENROUTER_API_KEY")
        provider = "OpenRouter" if openrouter_api_key else "Native (OpenAI/Anthropic/Google)"
        
        if openrouter_api_key:
            # When using OpenRouter, model selection is simplified
            # Default to gpt-4o-mini, but user can override via env vars
            return {
                "environment": "production",
                "provider": provider,
                "models": {
                    "manager": os.getenv("LLM_MODEL_MANAGER", "openai/gpt-4o-mini"),
                    "worker": os.getenv("LLM_MODEL_WORKER", "openai/gpt-4o-mini"),
                    "reviewer": os.getenv("LLM_MODEL_REVIEWER", "openai/gpt-4o-mini")
                },
                "cost": "Paid (cloud API)",
                "note": "OpenRouter handles model routing automatically. Model names are optional."
            }
        else:
            # Native providers require explicit model selection
            return {
                "environment": "production",
                "provider": provider,
                "models": {
                    "manager": os.getenv("LLM_MODEL_MANAGER", "gpt-4o"),
                    "worker": os.getenv("LLM_MODEL_WORKER", "gpt-4o-mini"),
                    "reviewer": os.getenv("LLM_MODEL_REVIEWER", "gpt-4o-mini")
                },
                "cost": "Paid (cloud API)"
            }


def print_llm_config():
    """Print current LLM configuration"""
    config = get_available_models()
    
    print("\n" + "="*60)
    print("🤖 LLM Configuration")
    print("="*60)
    
    if config["environment"] == "local":
        print(f"Environment: {config['environment'].upper()}")
        print(f"Provider: {config['provider']}")
        print(f"Base URL: {config['base_url']}")
        print(f"Model: {config['model']}")
        print(f"Cost: {config['cost']}")
    else:
        print(f"Environment: {config['environment'].upper()}")
        print(f"Provider: {config['provider']}")
        print(f"Manager agents: {config['models']['manager']}")
        print(f"Worker agents: {config['models']['worker']}")
        print(f"Reviewer agents: {config['models']['reviewer']}")
        print(f"Cost: {config['cost']}")
        if config['provider'] == "OpenRouter":
            print(f"Base URL: {OPENROUTER_BASE_URL}")
    
    print("="*60 + "\n")


