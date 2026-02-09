"""
LLM Configuration for AI Software Development Crew

Uses secure configuration from config files instead of environment variables.
Supports multiple OpenAI-compatible providers.
"""
import logging
from typing import Optional, Callable, Any
from llama_index.core.llms import LLM, LLMMetadata, ChatMessage, ChatResponse, CompletionResponse
from llama_index.llms.ollama import Ollama
from llama_index.embeddings.huggingface import HuggingFaceEmbedding

from ..config import SecretConfig

logger = logging.getLogger(__name__)


class GenericLlamaLLM(LLM):
    """
    A truly generic LLM class that uses the LlamaIndex core interfaces.
    It uses the OpenAI-compatible protocol via httpx directly to avoid 
    any OpenAI-specific library dependencies or validation logic.
    """
    model: str
    api_key: str
    api_base: str
    max_tokens: int
    temperature: float
    context_window: int

    def __init__(
        self, 
        model: str, 
        api_key: str, 
        api_base: str, 
        max_tokens: int = 2048, 
        temperature: float = 0.1,
        context_window: int = 4096,
        **kwargs
    ):
        super().__init__(
            model=model,
            api_key=api_key,
            api_base=api_base,
            max_tokens=max_tokens,
            temperature=temperature,
            context_window=context_window,
            **kwargs
        )

    @property
    def metadata(self) -> LLMMetadata:
        return LLMMetadata(
            context_window=self.context_window,
            num_output=self.max_tokens,
            is_chat_model=True,
            model_name=self.model,
        )

    def chat(self, messages: list[ChatMessage], **kwargs) -> ChatResponse:
        import httpx
        import time
        from llama_index.core.llms import ChatResponse, ChatMessage, MessageRole
        
        url = f"{self.api_base.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        # Filter out any kwargs that shouldn't go to the API
        api_kwargs = {k: v for k, v in kwargs.items() if k not in ["num_beams"]}
        
        # Ensure conversation roles alternate user/assistant
        # Red Hat MaaS (LiteLLM) is strict about role alternation and 'system' role.
        formatted_messages = []
        last_role = None
        
        for m in messages:
            role = m.role.value
            content = m.content
            
            # Red Hat MaaS (LiteLLM) is strict about role alternation and 'system' role.
            # Convert 'system' to 'user' if it's not the first message.
            if role == "system" and last_role is not None:
                role = "user"
            
            # If the role is the same as the last one, merge the content
            if role == last_role and formatted_messages:
                formatted_messages[-1]["content"] += f"\n\n{content}"
            else:
                formatted_messages.append({"role": role, "content": content})
                last_role = role

        # Ensure the conversation starts with a 'user' or 'system' message
        if formatted_messages and (formatted_messages[0]["role"] == "assistant"):
            formatted_messages.insert(0, {"role": "user", "content": "Continue."})
            
        # Ensure it ends with a 'user' message (some providers require this)
        # BUT: If the last message is already a tool call (assistant), don't add a user message
        # as it breaks the ReAct flow where the system expects to execute the tool first.
        if formatted_messages and formatted_messages[-1]["role"] == "assistant":
            # Only add "Please provide final answer" if it doesn't look like a tool call
            last_content = formatted_messages[-1]["content"]
            if "Action:" not in last_content and "Action Input:" not in last_content:
                formatted_messages.append({"role": "user", "content": "Please provide your final answer or next step."})
            else:
                # If it IS a tool call, we MUST NOT add a user message, but we might need to 
                # ensure the provider accepts the assistant message as the last one.
                # Most OpenAI-compatible providers do.
                pass

        payload = {
            "model": self.model,
            "messages": formatted_messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            **api_kwargs
        }
        
        max_retries = 5
        base_delay = 5
        max_delay = 120
        import random
        
        # Transient errors: connection/transport and timeouts (catch all timeout variants)
        retryable_exceptions = (httpx.RemoteProtocolError, httpx.ConnectError, httpx.ReadTimeout)
        if getattr(httpx, "TimeoutException", None) is not None:
            retryable_exceptions = retryable_exceptions + (httpx.TimeoutException,)
        
        for attempt in range(max_retries):
            try:
                with httpx.Client(timeout=120.0) as client:
                    logger.debug(f"Sending request to {url} with model {self.model} (Attempt {attempt + 1}/{max_retries})")
                    response = client.post(url, headers=headers, json=payload)
                    
                    if response.status_code != 200:
                        # Retry on server errors (5xx) and rate limit (429)
                        if response.status_code in (429, 502, 503, 504) and attempt < max_retries - 1:
                            delay = min(base_delay * (2 ** attempt) + random.uniform(0, 3), max_delay)
                            logger.warning(
                                f"⚠️ LLM API returned {response.status_code}. Retrying in {delay:.1f}s... (attempt {attempt + 1}/{max_retries})"
                            )
                            time.sleep(delay)
                            continue
                        logger.error(f"LLM API Error: {response.status_code} - {response.text[:500]}")
                    
                    response.raise_for_status()
                    data = response.json()
                    
                    choice = data["choices"][0]
                    content = choice["message"]["content"]
                    logger.debug(f"LLM Response: {content[:200]}...")
                    
                    return ChatResponse(
                        message=ChatMessage(
                            role=MessageRole.ASSISTANT,
                            content=content
                        ),
                        raw=data
                    )
            except retryable_exceptions as e:
                if attempt < max_retries - 1:
                    delay = min(base_delay * (2 ** attempt) + random.uniform(0, 3), max_delay)
                    logger.warning(
                        f"⚠️ LLM API connection error ({type(e).__name__}): {e}. Retrying in {delay:.1f}s... (attempt {attempt + 1}/{max_retries})"
                    )
                    time.sleep(delay)
                else:
                    logger.error(f"❌ LLM API failed after {max_retries} attempts: {e}")
                    raise

    async def achat(self, messages: list[ChatMessage], **kwargs) -> ChatResponse:
        import httpx
        from llama_index.core.llms import ChatResponse, ChatMessage, MessageRole
        
        url = f"{self.api_base.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": self.model,
            "messages": [{"role": m.role.value, "content": m.content} for m in messages],
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            **kwargs
        }
        
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            
            choice = data["choices"][0]
            return ChatResponse(
                message=ChatMessage(
                    role=MessageRole.ASSISTANT,
                    content=choice["message"]["content"]
                ),
                raw=data
            )

    def complete(self, prompt: str, **kwargs) -> CompletionResponse:
        import httpx
        import time
        import random
        from llama_index.core.llms import CompletionResponse
        
        url = f"{self.api_base.rstrip('/')}/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": self.model,
            "prompt": prompt,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            **kwargs
        }
        
        max_retries = 5
        base_delay = 5
        max_delay = 120
        retryable = (httpx.RemoteProtocolError, httpx.ConnectError, httpx.ReadTimeout)
        if getattr(httpx, "TimeoutException", None) is not None:
            retryable = retryable + (httpx.TimeoutException,)
        
        for attempt in range(max_retries):
            try:
                with httpx.Client(timeout=120.0) as client:
                    response = client.post(url, headers=headers, json=payload)
                    if response.status_code in (429, 502, 503, 504) and attempt < max_retries - 1:
                        delay = min(base_delay * (2 ** attempt) + random.uniform(0, 3), max_delay)
                        logger.warning(f"⚠️ LLM complete() got {response.status_code}, retrying in {delay:.1f}s...")
                        time.sleep(delay)
                        continue
                    response.raise_for_status()
                    data = response.json()
                    return CompletionResponse(
                        text=data["choices"][0]["text"],
                        raw=data
                    )
            except retryable as e:
                if attempt < max_retries - 1:
                    delay = min(base_delay * (2 ** attempt) + random.uniform(0, 3), max_delay)
                    logger.warning(f"⚠️ LLM complete() connection error: {e}. Retrying in {delay:.1f}s...")
                    time.sleep(delay)
                else:
                    raise

    async def acomplete(self, prompt: str, **kwargs) -> CompletionResponse:
        import httpx
        from llama_index.core.llms import CompletionResponse
        
        url = f"{self.api_base.rstrip('/')}/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": self.model,
            "prompt": prompt,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            **kwargs
        }
        
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            
            return CompletionResponse(
                text=data["choices"][0]["text"],
                raw=data
            )

    def stream_chat(self, messages, **kwargs):
        raise NotImplementedError("Streaming not implemented in generic wrapper")

    def stream_complete(self, prompt, **kwargs):
        raise NotImplementedError("Streaming not implemented in generic wrapper")

    async def astream_chat(self, messages, **kwargs):
        raise NotImplementedError("Async streaming not implemented in generic wrapper")

    async def astream_complete(self, prompt, **kwargs):
        raise NotImplementedError("Async streaming not implemented in generic wrapper")


def get_llm_for_agent(agent_type: str = "worker", config: Optional[SecretConfig] = None, budget_callback: Optional[Callable] = None):
    """
    Get LLM for specific agent type
    
    Args:
        agent_type: Type of agent (manager, worker, reviewer)
        config: SecretConfig instance (auto-loads if not provided)
        budget_callback: Optional callback for budget tracking
    
    Returns:
        Configured LLM instance
    """
    if config is None:
        from ..config import ConfigLoader
        config = ConfigLoader.load()
    
    if config.llm.environment.lower() == "local":
        return _get_local_llm(config)
    else:
        return _get_production_llm(agent_type, config, budget_callback)


def _get_local_llm(config: SecretConfig):
    """Get local Ollama LLM"""
    logger.info(f"🏠 Using local Ollama: {config.llm.ollama_model} at {config.llm.ollama_base_url}")
    
    return Ollama(
        model=config.llm.ollama_model,
        base_url=config.llm.ollama_base_url,
        temperature=config.llm.temperature,
        request_timeout=120.0
    )


def _get_production_llm(agent_type: str, config: SecretConfig, budget_callback: Optional[Callable] = None):
    """
    Get production LLM for any OpenAI-compatible provider
    
    Args:
        agent_type: Type of agent (manager, worker, reviewer)
        config: SecretConfig instance
        budget_callback: Optional callback for budget tracking
    
    Returns:
        Configured OpenAI-compatible LLM instance
    """
    # Get model based on agent type
    model_map = {
        "manager": config.llm.model_manager,
        "worker": config.llm.model_worker,
        "reviewer": config.llm.model_reviewer,
    }
    model = model_map.get(agent_type, config.llm.model_worker)
    
    # Log configuration
    provider_name = _get_provider_name(config.llm.api_base_url)
    logger.info(f"☁️  Using {provider_name} for {agent_type} agent")
    logger.info(f"   Model: {model}")
    if config.llm.api_base_url:
        logger.info(f"   Base URL: {config.llm.api_base_url}")
    
    # Configure LLM
    llm_kwargs = {
        "model": model,
        "api_key": config.llm.api_key,
        "max_tokens": config.llm.max_tokens,
        "temperature": config.llm.temperature,
        "timeout": 120.0,
        "reuse_client": False  # Ensure fresh client for each agent
    }
    
    # Use our truly generic Llama-based LLM wrapper
    if config.llm.api_base_url:
        llm_kwargs["api_base"] = config.llm.api_base_url
        
        # Set context window based on model info
        if "codellama" in model.lower() or "phi-4" in model.lower():
            llm_kwargs["context_window"] = 4000
        elif "qwen3" in model.lower() or "llama-scout" in model.lower():
            llm_kwargs["context_window"] = 400000
        else:
            # Default for granite, deepseek-r1, llama-guard which have 4M context
            llm_kwargs["context_window"] = 4000000
            
        return GenericLlamaLLM(**llm_kwargs)
    
    # Even for standard OpenAI, we use the generic wrapper to stay consistent with Llama framework
    llm_kwargs["api_base"] = "https://api.openai.com/v1"
    llm_kwargs["context_window"] = 128000 # Default for GPT-4
    return GenericLlamaLLM(**llm_kwargs)
    
    return OpenAI(**llm_kwargs)


def _get_provider_name(api_base: Optional[str] = None) -> str:
    """Get friendly provider name from base URL"""
    if not api_base:
        return "OpenAI"
    
    api_base_lower = api_base.lower()
    
    if "maas" in api_base_lower or "redhat" in api_base_lower:
        return "Red Hat MaaS"
    elif "openrouter" in api_base_lower:
        return "OpenRouter"
    elif "azure" in api_base_lower:
        return "Azure OpenAI"
    elif "localhost" in api_base_lower or "127.0.0.1" in api_base_lower:
        return "Local LiteLLM"
    else:
        return f"Custom Provider ({api_base})"


def get_embedding_model(config: Optional[SecretConfig] = None):
    """Get embedding model based on configuration"""
    if config is None:
        from ..config import ConfigLoader
        config = ConfigLoader.load()
    
    # Always use local HuggingFace embeddings to stay true to open source Llama framework
    # and avoid any external API dependencies for vector operations.
    logger.info("🏠 Using local HuggingFace embeddings (BAAI/bge-small-en-v1.5)")
    return HuggingFaceEmbedding(
        model_name="BAAI/bge-small-en-v1.5"
    )


def print_llm_config(config: Optional[SecretConfig] = None):
    """Print current LLM configuration for debugging"""
    if config is None:
        from ..config import ConfigLoader
        config = ConfigLoader.load()
    
    from ..config.secure_config import print_config_info
    print_config_info(config)

