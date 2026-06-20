"""
LLM Configuration for AI Software Development Crew

Uses secure configuration from config files instead of environment variables.
Supports multiple OpenAI-compatible providers.
"""
import logging
import socket
from typing import Optional, Callable
from llama_index.core.llms import LLM, LLMMetadata, ChatMessage, ChatResponse, CompletionResponse
from llama_index.llms.ollama import Ollama
from llama_index.embeddings.huggingface import HuggingFaceEmbedding

from ..config import SecretConfig
from .prompt_budget import trim_text, estimate_tokens

logger = logging.getLogger(__name__)

# Global safety net: cap every OS-level socket read at 300 s.
# httpx.Timeout handles the httpx layer, but if TLS session state resets the
# per-socket timeout after the handshake, Python's ssl._SSLObject.read() can
# block indefinitely.  socket.setdefaulttimeout() is applied to all new sockets
# created in this process, ensuring a hard ceiling even when httpx can't act.
socket.setdefaulttimeout(300)


def _trim_payload_for_context(payload: dict, trim_fraction: float = 0.25) -> dict:
    """
    Return a copy of *payload* with the last user message trimmed by *trim_fraction*.

    Called when the API returns a 400 context-length error so we can retry
    without rebuilding the entire call stack.  Only trims the final user message
    because that is where dynamic content (prompts, file listings) lives.
    Returns the original payload unchanged if there is nothing to trim.
    """
    import copy
    messages = payload.get("messages", [])
    if not messages:
        return payload

    # Find the last user message (index from end)
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            original = messages[i]["content"]
            if not original:
                break
            keep_chars = int(len(original) * (1.0 - trim_fraction))
            trimmed = original[:keep_chars] + "\n[... trimmed to fit context window ...]"
            new_payload = copy.deepcopy(payload)
            new_payload["messages"][i]["content"] = trimmed
            logger.info(
                "_trim_payload_for_context: trimmed last user message %d → %d chars (%.0f%%)",
                len(original), len(trimmed), trim_fraction * 100,
            )
            return new_payload

    return payload  # nothing to trim


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

    def _completions_url(self) -> str:
        """Build the chat completions URL, normalising the base to include /v1."""
        base = self.api_base.rstrip("/")
        if not base.endswith("/v1"):
            base = f"{base}/v1"
        return f"{base}/chat/completions"

    def chat(self, messages: list[ChatMessage], **kwargs) -> ChatResponse:
        import httpx
        import time
        from llama_index.core.llms import ChatResponse, ChatMessage, MessageRole
        
        url = self._completions_url()
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        # Filter out any kwargs that shouldn't go to the API
        api_kwargs = {k: v for k, v in kwargs.items() if k not in ["num_beams"]}
        formatted_messages = self._format_messages_for_api(messages)

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
        # Granular timeouts:  connect quickly, allow MaaS up to 5 min for 8k-token responses,
        # but cap any individual read() syscall at 120 s so a stalled SSL socket doesn't hang
        # the entire pytest session past the overall test timeout.
        request_timeout = httpx.Timeout(
            connect=30.0,   # DNS + TCP handshake
            write=60.0,     # sending the request body
            read=240.0,     # waiting for the first byte + streaming the full response
            pool=10.0,      # acquiring a connection from the pool
        )
        import random
        
        # Transient errors: connection/transport and timeouts.
        # socket.timeout (= TimeoutError = OSError with ETIMEDOUT) is raised when the
        # OS-level SSL socket read stalls — it is distinct from httpx.ReadTimeout and
        # was previously unhandled, causing the process to hang until pytest killed it.
        retryable_exceptions = (
            httpx.RemoteProtocolError,
            httpx.ConnectError,
            httpx.ReadTimeout,
            socket.timeout,    # OS-level SSL/TCP stall (Python 3.3+ alias: TimeoutError)
            TimeoutError,      # same as socket.timeout on Python 3.3+, explicit for clarity
        )
        if getattr(httpx, "TimeoutException", None) is not None:
            retryable_exceptions = retryable_exceptions + (httpx.TimeoutException,)
        
        for attempt in range(max_retries):
            try:
                with httpx.Client(timeout=request_timeout) as client:
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
                        # 400: check for context-length overflow and retry with trimmed input.
                        # Error pattern: "input tokens ... output tokens ... context length is only N"
                        if response.status_code == 400 and attempt < max_retries - 1:
                            body = response.text
                            _ctx_overflow = (
                                "input tokens" in body
                                or "context length" in body
                                or "context_length" in body
                                or "maximum context" in body
                                or "reduce the length" in body
                            )
                            if _ctx_overflow:
                                # Trim the last user message in the payload to free up tokens
                                _trimmed = _trim_payload_for_context(payload, trim_fraction=0.25)
                                if _trimmed is not payload:
                                    payload = _trimmed
                                    logger.warning(
                                        "⚠️ Context-length 400: trimmed prompt by ~25%%. Retrying... "
                                        "(attempt %d/%d)", attempt + 1, max_retries,
                                    )
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
            except Exception as e:
                # Catch-all for transient transport errors not covered by retryable_exceptions
                # (e.g. "Server disconnected", "without sending a response", stalled connections).
                msg = str(e).lower()
                _transient = (
                    "disconnect" in msg
                    or "without sending" in msg
                    or "connection" in msg
                    or "timeout" in msg      # catches "timed out", "ssl read timed out", etc.
                    or "reset" in msg        # "connection reset by peer"
                )
                if _transient and attempt < max_retries - 1:
                    delay = min(base_delay * (2 ** attempt) + random.uniform(0, 3), max_delay)
                    logger.warning(
                        f"⚠️ LLM API error ({type(e).__name__}): {e}. Retrying in {delay:.1f}s... (attempt {attempt + 1}/{max_retries})"
                    )
                    time.sleep(delay)
                else:
                    raise

    def _format_messages_for_api(self, messages: list) -> list:
        """Apply same role alternation and system→user rules as chat() so MaaS accepts the payload."""
        formatted_messages = []
        last_role = None
        for m in messages:
            role = m.role.value
            content = m.content
            if role == "system" and last_role is not None:
                role = "user"
            if role == last_role and formatted_messages:
                formatted_messages[-1]["content"] += f"\n\n{content}"
            else:
                formatted_messages.append({"role": role, "content": content})
                last_role = role
        if formatted_messages and formatted_messages[0]["role"] == "assistant":
            formatted_messages.insert(0, {"role": "user", "content": "Continue."})
        if formatted_messages and formatted_messages[-1]["role"] == "assistant":
            last_content = formatted_messages[-1]["content"]
            if "Action:" not in last_content and "Action Input:" not in last_content:
                formatted_messages.append({"role": "user", "content": "Please provide your final answer or next step."})
        return formatted_messages

    async def achat(self, messages: list[ChatMessage], **kwargs) -> ChatResponse:
        import httpx
        from llama_index.core.llms import ChatResponse, ChatMessage, MessageRole
        
        url = self._completions_url()
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        formatted_messages = self._format_messages_for_api(messages)
        api_kwargs = {k: v for k, v in kwargs.items() if k not in ["num_beams"]}
        payload = {
            "model": self.model,
            "messages": formatted_messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            **api_kwargs
        }
        
        # Granular timeouts to prevent SSL read stalls
        _async_timeout = httpx.Timeout(connect=30.0, write=60.0, read=240.0, pool=10.0)
        async with httpx.AsyncClient(timeout=_async_timeout) as client:
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
        retryable = (
            httpx.RemoteProtocolError,
            httpx.ConnectError,
            httpx.ReadTimeout,
            socket.timeout,
            TimeoutError,
        )
        if getattr(httpx, "TimeoutException", None) is not None:
            retryable = retryable + (httpx.TimeoutException,)
        
        _complete_timeout = httpx.Timeout(connect=30.0, write=60.0, read=180.0, pool=10.0)
        for attempt in range(max_retries):
            try:
                with httpx.Client(timeout=_complete_timeout) as client:
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
        
        _acomplete_timeout = httpx.Timeout(connect=30.0, write=60.0, read=180.0, pool=10.0)
        async with httpx.AsyncClient(timeout=_acomplete_timeout) as client:
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
        # MaaS LiteLLM proxy — detect model context window and cap max_tokens accordingly.
        # Rule: max_tokens ≤ (context_window / 2) - 1024  so the input prompt always has room.
        # deepseek-r1-distill-* models on MaaS have a 16 384-token context.
        # Setting max_tokens=8192 on a 16384-context model leaves only 8192 for input —
        # the TA prompt regularly exceeds this by a few tokens → immediate 400 Bad Request.
        is_maas = (
            "maas" in config.llm.api_base_url.lower()
            or "redhatworkshops" in config.llm.api_base_url.lower()
        )
        if is_maas:
            m = model.lower()
            if "deepseek-r1-distill" in m or "deepseek-r1" in m:
                # 16 384-token context — cap output to 6 144 so input can use up to 10 240
                model_ctx = 16_384
                maas_max_tokens = 6_144
            elif "codellama" in m or "phi-4" in m:
                model_ctx = 4_000
                maas_max_tokens = 1_024
            elif "qwen3" in m:
                model_ctx = 32_768
                maas_max_tokens = 8_192
            else:
                # Conservative default for unknown MaaS models
                model_ctx = 16_384
                maas_max_tokens = 6_144
            llm_kwargs["max_tokens"] = min(llm_kwargs["max_tokens"], maas_max_tokens)
            llm_kwargs["context_window"] = model_ctx
            logger.info(
                "   MaaS endpoint detected: context_window=%d, capping max_tokens to %d",
                model_ctx, llm_kwargs["max_tokens"],
            )
        else:
            # Non-MaaS: set context window based on model info
            m = model.lower()
            if "codellama" in m or "phi-4" in m:
                llm_kwargs["context_window"] = 4_000
            elif "qwen3" in m or "llama-scout" in m:
                llm_kwargs["context_window"] = 400_000
            else:
                llm_kwargs["context_window"] = 4_000_000
            
        return GenericLlamaLLM(**llm_kwargs)
    
    # Even for standard OpenAI, we use the generic wrapper to stay consistent with Llama framework
    llm_kwargs["api_base"] = "https://api.openai.com/v1"
    llm_kwargs["context_window"] = 128000 # Default for GPT-4
    return GenericLlamaLLM(**llm_kwargs)


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

