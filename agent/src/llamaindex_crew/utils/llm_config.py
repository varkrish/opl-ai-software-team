"""
LLM Configuration for AI Software Development Crew

Uses secure configuration from config files instead of environment variables.
Supports multiple OpenAI-compatible providers.
"""
import logging
import socket
import threading
from contextlib import contextmanager
from typing import Optional, Callable, Any
from llama_index.core.llms import LLM, LLMMetadata, ChatMessage, ChatResponse, CompletionResponse
from llama_index.core.llms.callbacks import llm_chat_callback, llm_completion_callback
from llama_index.llms.ollama import Ollama
from llama_index.embeddings.huggingface import HuggingFaceEmbedding

from ..config import SecretConfig
from .prompt_budget import trim_text, estimate_tokens

logger = logging.getLogger(__name__)


class MissingLLMAPIKeyError(ValueError):
    """Raised when a job would call a remote LLM without a usable API key."""


def ensure_llm_api_key(config: SecretConfig) -> None:
    """Fail fast if remote LLM credentials are missing.

    Local/Ollama mode does not require an API key. Remote providers do —
    an empty key produces ``Authorization: Bearer `` which httpx rejects as
    ``Illegal header value b'Bearer '``.
    """
    if config is None:
        raise MissingLLMAPIKeyError(
            "No LLM configuration loaded. Set llm.api_key in ~/.crew-ai/config.yaml "
            "or save a key in Settings → API Configuration, then restart the job."
        )
    if (config.llm.environment or "").lower() == "local":
        return
    key = (config.llm.api_key or "").strip() if isinstance(config.llm.api_key, str) else ""
    if not key:
        raise MissingLLMAPIKeyError(
            "No LLM API key configured. Set llm.api_key in ~/.crew-ai/config.yaml "
            "or save a key in Settings → API Configuration, then restart the job."
        )


def _normalize_api_key(api_key: Any) -> str:
    if api_key is None:
        return ""
    if isinstance(api_key, bytes):
        api_key = api_key.decode("utf-8", errors="replace")
    return str(api_key).strip()


# ---------------------------------------------------------------------------
# ReAct capability inference
# ---------------------------------------------------------------------------

# Model name substrings that indicate a weak/free model unable to sustain
# multi-turn ReAct tool loops reliably. Add patterns here as new free tiers
# or small quantised models are introduced. Matching is case-insensitive and
# uses substring search so "8b" catches "llama-3.1-8b-instruct" etc.
_WEAK_MODEL_PATTERNS: frozenset = frozenset({
    ":free",        # openrouter free tier  (e.g. "openai/gpt-4o:free")
    "/free",        # alternative separator (e.g. "meta-llama/llama-3:free")
    "-free",        # yet another separator
    "8b",           # 8-billion-parameter models
    "7b",           # 7-billion-parameter models
    "3b",           # 3-billion-parameter models
    "1b",           # 1-billion-parameter models
    "phi-3",        # Microsoft Phi-3 family (3.8B / 7B)
    "phi3",
    "gemma-2b",
    "gemma-7b",
    "mistral-7b",
    "codellama-7b",
    "deepseek-r1-distill-qwen-1",   # 1.5B distil
    "deepseek-r1-distill-qwen-7",   # 7B distil
})


def infer_supports_react(model_name: str) -> bool:
    """Return True if *model_name* is likely capable of ReAct tool loops.

    Uses a conservative substring-match heuristic against known weak-model
    patterns.  When in doubt, returns True so frontier models are not
    accidentally downgraded to single-shot mode.
    """
    name = (model_name or "").lower()
    for pattern in _WEAK_MODEL_PATTERNS:
        if pattern in name:
            logger.debug("infer_supports_react: model %r matched weak pattern %r → False", model_name, pattern)
            return False
    return True


def get_supports_react(agent_type: str = "worker", config: Optional["SecretConfig"] = None) -> bool:
    """Return True if the model assigned to *agent_type* can handle ReAct loops.

    Resolution order:
    1. Explicit ``config.llm.supports_react`` override (not None) → use as-is.
    2. Auto-infer from the model name for *agent_type*.

    Config is resolved from the thread-local override first, then loaded from
    disk — matching the same lookup order used by ``get_llm_for_agent()``.
    """
    if config is None:
        config = getattr(_thread_config, "config", None)
    if config is None:
        from ..config import ConfigLoader
        config = ConfigLoader.load()

    # Explicit global override takes precedence
    explicit = getattr(config.llm, "supports_react", None)
    if explicit is not None:
        return bool(explicit)

    # Auto-infer from per-role model name
    model_map = {
        "manager": config.llm.model_manager,
        "worker": config.llm.model_worker,
        "reviewer": config.llm.model_reviewer,
    }
    model = model_map.get(agent_type, config.llm.model_worker)
    result = infer_supports_react(model)
    logger.debug("get_supports_react(agent_type=%r, model=%r) → %s", agent_type, model, result)
    return result


# Global safety net: cap every OS-level socket read at 300 s.
# httpx.Timeout handles the httpx layer, but if TLS session state resets the
# per-socket timeout after the handshake, Python's ssl._SSLObject.read() can
# block indefinitely.  socket.setdefaulttimeout() is applied to all new sockets
# created in this process, ensuring a hard ceiling even when httpx can't act.
socket.setdefaulttimeout(300)


def _trim_payload_for_context(payload: dict, trim_fraction: float = 0.25) -> dict:
    """
    Return a copy of *payload* with the context trimmed to fit.

    Args:
        payload: The LLM API request payload containing a 'messages' list.
        trim_fraction: Fraction of the longest message to DROP (default 0.25 = drop 25%).
                       The test suite uses 0.25 and 0.50. The keep fraction is (1 - trim_fraction).

    Strategy:
    1. First, try to drop the oldest intermediate conversation history.
    2. If there is no history to drop (e.g., initial prompt is too large),
       trim the longest message from the MIDDLE. This preserves the start
       (the main task) and the end (the tool definitions and formatting rules),
       ensuring the agent doesn't hallucinate tool usage.
    """
    import copy
    messages = payload.get("messages", [])
    if not messages:
        return payload

    # Strategy 1: Drop old conversation history
    if len(messages) > 2:
        new_messages = []
        dropped = False
        for i, msg in enumerate(messages):
            if i > 0 and i < len(messages) - 1 and not dropped:
                logger.warning(
                    "Context chunker: dropping oldest intermediate message (role=%s) to free context",
                    msg.get("role")
                )
                dropped = True
                continue
            new_messages.append(msg)

        if dropped:
            new_payload = copy.deepcopy(payload)
            new_payload["messages"] = new_messages
            return new_payload

    # Strategy 2: Middle-trim the longest message (preserves tools at the end)
    keep_fraction = 1.0 - trim_fraction
    best_idx = -1
    best_score = -1

    for i, msg in enumerate(messages):
        content = msg.get("content", "")
        if not content: continue
        score = len(content)
        if msg.get("role") == "system":
            score = score * 0.3  # Penalize trimming system

        if score > best_score:
            best_score = score
            best_idx = i

    if best_idx >= 0:
        original = messages[best_idx]["content"]
        keep_chars = int(len(original) * keep_fraction)

        if keep_chars > 100:
            half = keep_chars // 2
            trimmed = original[:half] + "\n\n[... trimmed to fit context window ...]\n\n" + original[-half:]

            new_payload = copy.deepcopy(payload)
            new_messages = []
            for i, msg in enumerate(messages):
                if i == best_idx:
                    new_msg = dict(msg)
                    new_msg["content"] = trimmed
                    new_messages.append(new_msg)
                else:
                    new_messages.append(msg)
            new_payload["messages"] = new_messages

            logger.warning(
                "Context chunker: Middle-trimmed longest message (role=%s) %d \u2192 %d chars (%.0f%% drop) to preserve tool schemas",
                messages[best_idx].get("role"), len(original), len(trimmed), trim_fraction * 100
            )
            return new_payload

    return payload


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
            is_function_calling_model=True,
            model_name=self.model,
        )

    # ------------------------------------------------------------------
    # Function-calling interface (used by FunctionCallingAgentWorker)
    # ------------------------------------------------------------------

    def chat_with_tools(
        self,
        tools,
        user_msg=None,
        chat_history=None,
        verbose: bool = False,
        allow_parallel_tool_calls: bool = False,
        **kwargs,
    ) -> ChatResponse:
        """Call the LLM with tools in OpenAI-compatible format."""
        from llama_index.core.llms import ChatMessage

        tool_specs = []
        for tool in tools:
            fn_schema = {"type": "object", "properties": {}, "required": []}
            if hasattr(tool, "metadata"):
                try:
                    fn_schema = tool.metadata.get_parameters_dict()
                except Exception:
                    pass
            tool_specs.append({
                "type": "function",
                "function": {
                    "name": tool.metadata.name if hasattr(tool, "metadata") else str(tool),
                    "description": (tool.metadata.description or "") if hasattr(tool, "metadata") else "",
                    "parameters": fn_schema,
                },
            })

        messages = list(chat_history or [])
        if user_msg is not None:
            if isinstance(user_msg, str):
                messages.append(ChatMessage(role="user", content=user_msg))
            else:
                messages.append(user_msg)

        return self.chat(messages, tools=tool_specs, **kwargs)

    def get_tool_calls_from_response(
        self,
        response: ChatResponse,
        error_on_no_tool_call: bool = True,
        **kwargs,
    ):
        """Extract tool calls from a ChatResponse.

        Primary: structured ``tool_calls`` in response additional_kwargs.
        Fallback: regex extraction from content text when the model embeds
        tool calls in its output instead of using the API field. This makes
        the agent resilient to models that intermittently drop out of
        structured function-calling mode.
        """
        import json as _json
        import re as _re
        from llama_index.core.tools import ToolSelection

        tool_calls_raw = (response.message.additional_kwargs or {}).get("tool_calls", [])
        selections = []
        for tc in tool_calls_raw:
            fn = tc.get("function", {})
            try:
                tool_kwargs = _json.loads(fn.get("arguments", "{}"))
            except (ValueError, TypeError):
                tool_kwargs = {}
            selections.append(ToolSelection(
                tool_id=tc.get("id", f"call_{id(tc)}"),
                tool_name=fn.get("name", ""),
                tool_kwargs=tool_kwargs,
            ))

        if not selections:
            content = response.message.content or ""
            selections = self._extract_tool_calls_from_content(content)
            if selections:
                logger.info(
                    "Recovered %d tool call(s) from content fallback: %s",
                    len(selections),
                    [s.tool_name for s in selections],
                )
                # Inject synthetic tool_calls into the response message so the
                # conversation history stays valid (API requires assistant
                # messages with tool_calls before tool-result messages).
                import json as _json2
                synthetic_calls = []
                for sel in selections:
                    synthetic_calls.append({
                        "id": sel.tool_id,
                        "type": "function",
                        "function": {
                            "name": sel.tool_name,
                            "arguments": _json2.dumps(sel.tool_kwargs),
                        },
                    })
                if not response.message.additional_kwargs:
                    response.message.additional_kwargs = {}
                response.message.additional_kwargs["tool_calls"] = synthetic_calls
                response.message.content = None

        if not selections and error_on_no_tool_call:
            raise ValueError(
                f"No tool calls in LLM response. Content: {str(response.message.content)[:200]}"
            )
        return selections

    @staticmethod
    def _extract_tool_calls_from_content(content: str):
        """Best-effort extraction of tool calls embedded in model text output.

        Handles patterns like:
          - ``<|message|>{"file_path":"x"}<|call|>`` with tool name nearby
          - ``Action: tool_name\\nAction Input: {...}`` (ReAct format)
        """
        import json as _json
        import re as _re
        import uuid as _uuid
        from llama_index.core.tools import ToolSelection

        results = []

        # Pattern 1: model-internal markup  <...>to=functions.TOOL_NAME<...><|message|>JSON<|call|>
        for m in _re.finditer(
            r'to=(?:functions\.)?(\w+)[^<]*<\|message\|>\s*(\{[^}]+\})\s*<\|call\|>',
            content,
        ):
            tool_name = m.group(1)
            try:
                tool_kwargs = _json.loads(m.group(2))
            except (ValueError, TypeError):
                continue
            results.append(ToolSelection(
                tool_id=f"recovered_{_uuid.uuid4().hex[:8]}",
                tool_name=tool_name,
                tool_kwargs=tool_kwargs,
            ))

        # Pattern 2: ReAct format in content
        if not results:
            for m in _re.finditer(
                r'Action:\s*(\w+)\s*\nAction Input:\s*(\{.+?\})',
                content, _re.DOTALL,
            ):
                tool_name = m.group(1)
                try:
                    tool_kwargs = _json.loads(m.group(2))
                except (ValueError, TypeError):
                    continue
                results.append(ToolSelection(
                    tool_id=f"recovered_{_uuid.uuid4().hex[:8]}",
                    tool_name=tool_name,
                    tool_kwargs=tool_kwargs,
                ))

        return results

    # ------------------------------------------------------------------

    def _completions_url(self) -> str:
        """Build the chat completions URL, normalising the base to include /v1."""
        base = self.api_base.rstrip("/")
        if not base.endswith("/v1"):
            base = f"{base}/v1"
        return f"{base}/chat/completions"

    @llm_chat_callback()
    def chat(self, messages: list[ChatMessage], **kwargs) -> ChatResponse:
        import httpx
        import time
        from llama_index.core.llms import ChatResponse, ChatMessage, MessageRole
        
        url = self._completions_url()
        headers = {
            "Content-Type": "application/json"
        }
        api_key = _normalize_api_key(self.api_key)
        if not api_key:
            raise MissingLLMAPIKeyError(
                "No LLM API key configured. Set llm.api_key in ~/.crew-ai/config.yaml "
                "or save a key in Settings → API Configuration, then restart the job."
            )
        headers["Authorization"] = f"Bearer {api_key}"
        
        _PAYLOAD_EXCLUDE_KEYS = {
            "num_beams", "tools", "tool_choice",
            "verbose", "allow_parallel_tool_calls",
        }
        api_kwargs = {k: v for k, v in kwargs.items() if k not in _PAYLOAD_EXCLUDE_KEYS}
        formatted_messages = self._format_messages_for_api(messages)

        payload = {
            "model": self.model,
            "messages": formatted_messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            **api_kwargs
        }

        if "tools" in kwargs and kwargs["tools"]:
            payload["tools"] = kwargs["tools"]
            if "tool_choice" in kwargs:
                payload["tool_choice"] = kwargs["tool_choice"]
            logger.info("Function-calling: %d tool(s) in payload", len(payload["tools"]))
        
        # --- Pre-flight token trimming to avoid 400 errors ---
        from .prompt_budget import PromptBudget, estimate_tokens
        budget = PromptBudget.from_context(self.context_window, self.max_tokens)
        
        def _get_payload_text(p: dict) -> str:
            return "\n".join(m.get("content", "") for m in p.get("messages", []) if isinstance(m.get("content"), str))
            
        estimated_tokens = estimate_tokens(_get_payload_text(payload))
        if estimated_tokens > budget.input_token_budget:
            logger.info(
                "Pre-trimming context: Estimated %d tokens > budget %d. Trimming locally to save API roundtrip.",
                estimated_tokens, budget.input_token_budget
            )
            while estimate_tokens(_get_payload_text(payload)) > budget.input_token_budget:
                trimmed = _trim_payload_for_context(payload)
                if trimmed is payload:
                    logger.warning("Pre-trimming: Cannot trim payload any further safely.")
                    break
                payload = trimmed
        # -----------------------------------------------------
        
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
                                _trimmed = _trim_payload_for_context(payload)
                                if _trimmed is not payload:
                                    payload = _trimmed
                                    logger.warning(
                                        "⚠️ Context-length 400: trimmed prompt by ~25%%. Retrying... "
                                        "(attempt %d/%d)", attempt + 1, max_retries,
                                    )
                                    continue
                        logger.error(f"LLM API Error: {response.status_code} - {response.text[:500]}")
                        raise ValueError(f"HTTP {response.status_code}: {response.text[:200]}")
                    
                    response.raise_for_status()
                    data = response.json()
                    
                    if not data or not data.get("choices"):
                        err_msg = data.get("error", {}).get("message") if isinstance(data.get("error"), dict) else str(data)
                        logger.error(f"Missing 'choices' in LLM response. Raw data: {data}")
                        raise ValueError(f"LLM returned invalid format: {err_msg}")
                        
                    choice = data["choices"][0]
                    msg_data = choice.get("message", {})
                    content = msg_data.get("content") or ""
                    logger.debug(f"LLM Response: {content[:200]}...")

                    additional_kwargs: dict = {}
                    if msg_data.get("tool_calls"):
                        additional_kwargs["tool_calls"] = msg_data["tool_calls"]

                    return ChatResponse(
                        message=ChatMessage(
                            role=MessageRole.ASSISTANT,
                            content=content,
                            additional_kwargs=additional_kwargs,
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
        """Apply role alternation and system→user rules so MaaS endpoints accept the payload.

        Rules:
        - Consecutive messages with the *same original* role are merged (except
          tool/assistant-with-tool_calls which must stay separate for the API).
        - A system message that immediately follows another system message is merged
          into the existing system block (stays as system).
        - A system message that appears after any non-system message is converted
          to 'user' but kept as a separate entry.
        - An 'assistant' message is never the first or last message (guards added).
        - Tool-call messages (role=tool) are passed through verbatim.
        - Assistant messages carrying tool_calls preserve that field.
        """
        formatted_messages = []
        last_original_role = None
        for m in messages:
            original_role = m.role.value if hasattr(m.role, "value") else str(m.role)
            content = m.content or ""
            extra = getattr(m, "additional_kwargs", None) or {}

            # tool-result messages: pass through with tool_call_id
            if original_role == "tool":
                entry = {"role": "tool", "content": content}
                if extra.get("tool_call_id"):
                    entry["tool_call_id"] = extra["tool_call_id"]
                elif extra.get("name"):
                    entry["tool_call_id"] = extra.get("tool_call_id", extra["name"])
                formatted_messages.append(entry)
                last_original_role = "tool"
                continue

            # assistant message with tool_calls: preserve tool_calls, never merge
            if original_role == "assistant" and extra.get("tool_calls"):
                entry = {"role": "assistant", "content": content or None, "tool_calls": extra["tool_calls"]}
                formatted_messages.append(entry)
                last_original_role = "assistant"
                continue

            # System after non-system → convert to user for MaaS compatibility
            if original_role == "system" and last_original_role is not None and last_original_role != "system":
                api_role = "user"
            else:
                api_role = original_role

            # Merge only when the *original* role matches the previous original role
            if original_role == last_original_role and formatted_messages and original_role not in ("tool",):
                formatted_messages[-1]["content"] += f"\n\n{content}"
            else:
                formatted_messages.append({"role": api_role, "content": content})
                last_original_role = original_role

        if formatted_messages and formatted_messages[0].get("role") == "assistant":
            formatted_messages.insert(0, {"role": "user", "content": "Continue."})
        if formatted_messages and formatted_messages[-1].get("role") == "assistant":
            last_entry = formatted_messages[-1]
            if not last_entry.get("tool_calls"):
                last_content = last_entry.get("content", "")
                if "Action:" not in last_content and "Action Input:" not in last_content:
                    formatted_messages.append({"role": "user", "content": "Please provide your final answer or next step."})
        return formatted_messages

    @llm_chat_callback()
    async def achat(self, messages: list[ChatMessage], **kwargs) -> ChatResponse:
        import httpx
        from llama_index.core.llms import ChatResponse, ChatMessage, MessageRole
        
        url = self._completions_url()
        headers = {
            "Content-Type": "application/json"
        }
        api_key = _normalize_api_key(self.api_key)
        if not api_key:
            raise MissingLLMAPIKeyError(
                "No LLM API key configured. Set llm.api_key in ~/.crew-ai/config.yaml "
                "or save a key in Settings → API Configuration, then restart the job."
            )
        headers["Authorization"] = f"Bearer {api_key}"
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
            if response.status_code != 200:
                raise ValueError(f"HTTP {response.status_code}: {response.text[:200]}")
            response.raise_for_status()
            data = response.json()
            
            if not data or not data.get("choices"):
                err_msg = data.get("error", {}).get("message") if isinstance(data.get("error"), dict) else str(data)
                raise ValueError(f"LLM returned invalid format: {err_msg}")
                
            choice = data["choices"][0]
            return ChatResponse(
                message=ChatMessage(
                    role=MessageRole.ASSISTANT,
                    content=choice["message"]["content"]
                ),
                raw=data
            )

    @llm_completion_callback()
    def complete(self, prompt: str, **kwargs) -> CompletionResponse:
        import httpx
        import time
        import random
        from llama_index.core.llms import CompletionResponse
        
        url = f"{self.api_base.rstrip('/')}/completions"
        headers = {
            "Content-Type": "application/json"
        }
        api_key = _normalize_api_key(self.api_key)
        if not api_key:
            raise MissingLLMAPIKeyError(
                "No LLM API key configured. Set llm.api_key in ~/.crew-ai/config.yaml "
                "or save a key in Settings → API Configuration, then restart the job."
            )
        headers["Authorization"] = f"Bearer {api_key}"
        
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
                    if response.status_code != 200:
                        raise ValueError(f"HTTP {response.status_code}: {response.text[:200]}")
                    response.raise_for_status()
                    data = response.json()
                    
                    if not data or not data.get("choices"):
                        err_msg = data.get("error", {}).get("message") if isinstance(data.get("error"), dict) else str(data)
                        raise ValueError(f"LLM returned invalid format: {err_msg}")
                        
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

    @llm_completion_callback()
    async def acomplete(self, prompt: str, **kwargs) -> CompletionResponse:
        import httpx
        from llama_index.core.llms import CompletionResponse
        
        url = f"{self.api_base.rstrip('/')}/completions"
        headers = {
            "Content-Type": "application/json"
        }
        api_key = _normalize_api_key(self.api_key)
        if not api_key:
            raise MissingLLMAPIKeyError(
                "No LLM API key configured. Set llm.api_key in ~/.crew-ai/config.yaml "
                "or save a key in Settings → API Configuration, then restart the job."
            )
        headers["Authorization"] = f"Bearer {api_key}"
        
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
            if response.status_code != 200:
                raise ValueError(f"HTTP {response.status_code}: {response.text[:200]}")
            response.raise_for_status()
            data = response.json()
            
            if not data or not data.get("choices"):
                err_msg = data.get("error", {}).get("message") if isinstance(data.get("error"), dict) else str(data)
                raise ValueError(f"LLM returned invalid format: {err_msg}")
                
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


_thread_config = threading.local()


def set_thread_config(config: SecretConfig) -> None:
    """Set the LLM config override for the current thread."""
    _thread_config.config = config


def clear_thread_config() -> None:
    """Clear the thread-local LLM config override."""
    _thread_config.config = None


@contextmanager
def user_llm_context(job_id: str, job_db: Any, fallback_config: SecretConfig):
    """Resolves and merges user-specific LLM config for the job owner, scoping it to this thread.

    Empty or undecryptable BYOK keys are ignored so they never wipe the server fallback.
    """
    job = job_db.get_job(job_id)
    owner_id = job.get("owner_id") if job else None
    if owner_id:
        user_llm = job_db.get_llm_config(owner_id)
        user_key = _normalize_api_key(user_llm.get("api_key") if user_llm else None)
        if user_llm and user_key:
            from copy import deepcopy
            merged = deepcopy(fallback_config)
            merged.llm.api_key = user_key
            merged.llm.api_base_url = user_llm["api_base_url"]
            merged.llm.model_manager = user_llm["model_manager"]
            merged.llm.model_worker = user_llm["model_worker"]
            merged.llm.model_reviewer = user_llm["model_reviewer"]
            set_thread_config(merged)
            try:
                yield merged
            finally:
                clear_thread_config()
            return
        if user_llm and not user_key:
            logger.warning(
                "User LLM config for owner %s has an empty API key; using server config",
                owner_id,
            )

    set_thread_config(fallback_config)
    try:
        yield fallback_config
    finally:
        clear_thread_config()


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
        config = getattr(_thread_config, 'config', None)
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
        # Check database for custom model context window mapping
        db_model_ctx = None
        try:
            import os
            from pathlib import Path
            from crew_studio.job_database import JobDatabase
            db_path = Path(os.getenv("JOB_DB_PATH", "./crew_jobs.db"))
            job_db = JobDatabase(db_path)
            db_model_ctx = job_db.get_model_context_window(model)
        except Exception as e:
            logger.debug("Could not query model context window from database: %s", e)

        if db_model_ctx is not None:
            llm_kwargs["context_window"] = db_model_ctx
            llm_kwargs["max_tokens"] = min(llm_kwargs["max_tokens"], max(1024, db_model_ctx // 2))
            logger.info("   Resolved context window from database: %d", db_model_ctx)
        elif is_maas:
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
            if "codellama" in m:
                llm_kwargs["context_window"] = 4_000
            elif "phi-4" in m:
                llm_kwargs["context_window"] = 16_384
            elif "qwen3" in m or "llama-scout" in m:
                llm_kwargs["context_window"] = 400_000
            elif "gpt-4o" in m or "gpt-4-turbo" in m:
                llm_kwargs["context_window"] = 128_000
            elif "claude-3-5" in m or "claude-3" in m:
                llm_kwargs["context_window"] = 200_000
            elif "gemini" in m:
                llm_kwargs["context_window"] = 1_000_000
            elif "llama-3.1" in m or "llama-3.2" in m:
                llm_kwargs["context_window"] = 128_000
            elif "deepseek-r1" in m or "deepseek-v3" in m:
                llm_kwargs["context_window"] = 128_000
            else:
                # Sensible default context window for custom models
                llm_kwargs["context_window"] = 128_000
            
            # Ensure max_tokens never consumes more than half the context window
            llm_kwargs["max_tokens"] = min(llm_kwargs["max_tokens"], max(1024, llm_kwargs["context_window"] // 2))
            
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

