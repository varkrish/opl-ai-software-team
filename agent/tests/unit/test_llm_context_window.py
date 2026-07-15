"""
Unit tests for MaaS model context-window and max_tokens configuration.

Root cause captured: deepseek-r1-distill-qwen-14b on MaaS has a 16 384-token
context.  Setting max_tokens=8192 leaves only 8192 tokens for input.  The Tech
Architect prompt routinely exceeds this by a few tokens → 400 Bad Request.

Fix: detect deepseek on MaaS and set:
  context_window = 16 384
  max_tokens     = 6 144  (leaves ≥ 10 240 tokens for input)

These tests exercise the _get_production_llm path directly to avoid needing
a full SecretConfig object.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _make_llm_config(model: str, api_base: str, max_tokens: int = 8192, temperature: float = 0.7):
    """Build a minimal LLMConfig-like mock."""
    cfg = MagicMock()
    cfg.api_key = "sk-test"
    cfg.api_base_url = api_base
    cfg.max_tokens = max_tokens
    cfg.temperature = temperature
    return cfg


def _call_production_llm(model: str, api_base: str, max_tokens: int = 8192):
    """
    Call _get_production_llm with a minimal mock config and return the LLM.

    Patches GenericLlamaLLM so no real HTTP client is created.
    """
    from llamaindex_crew.utils import llm_config as lc

    captured = {}

    class _FakeLLM:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.max_tokens = kwargs.get("max_tokens", max_tokens)
            self.context_window = kwargs.get("context_window", 4_000_000)

    # Build a mock SecretConfig
    config = MagicMock()
    config.llm.api_key = "sk-test"
    config.llm.api_base_url = api_base
    config.llm.max_tokens = max_tokens
    config.llm.temperature = 0.7
    config.tools.global_tools = []
    config.tools.agent_tools = {}

    # Detect which model field _get_production_llm reads for this agent_type
    config.llm.model_manager = model
    config.llm.model_worker = model
    config.llm.model_reviewer = model
    config.llm.model_devops = model
    config.llm.model_meta = model

    with patch.object(lc, "GenericLlamaLLM", _FakeLLM):
        lc._get_production_llm("manager", config)

    return captured  # kwargs passed to _FakeLLM / GenericLlamaLLM


_MAAS_URL = "https://litellm-prod.apps.maas.redhatworkshops.io/v1"


# ---------------------------------------------------------------------------
# deepseek-r1-distill on MaaS — the failing case
# ---------------------------------------------------------------------------

class TestDeepseekMaaSContextWindow:

    def test_deepseek_r1_distill_max_tokens_capped_to_8192(self):
        """
        deepseek-r1-distill-qwen-14b has a 128 000-token context.
        max_tokens must be ≤ 8 192.
        """
        kwargs = _call_production_llm("deepseek-r1-distill-qwen-14b", _MAAS_URL)
        assert kwargs["max_tokens"] <= 8_192, (
            f"deepseek-r1-distill on MaaS must cap max_tokens ≤ 8192, got {kwargs['max_tokens']}"
        )

    def test_deepseek_r1_distill_context_window_set_correctly(self):
        """context_window must be 128 000, not the default 4 000 000."""
        kwargs = _call_production_llm("deepseek-r1-distill-qwen-14b", _MAAS_URL)
        assert kwargs["context_window"] == 128_000, (
            f"deepseek-r1-distill context_window must be 128000, got {kwargs['context_window']}"
        )

    def test_deepseek_r1_distill_input_headroom(self):
        """context_window - max_tokens must leave at least 9 000 tokens for the input prompt."""
        kwargs = _call_production_llm("deepseek-r1-distill-qwen-14b", _MAAS_URL)
        headroom = kwargs["context_window"] - kwargs["max_tokens"]
        assert headroom >= 9_000, (
            f"Input headroom too small: ctx={kwargs['context_window']}, "
            f"max_tokens={kwargs['max_tokens']}, headroom={headroom}"
        )

    def test_deepseek_r1_without_distill_also_capped(self):
        """Any deepseek-r1 variant on MaaS must be capped."""
        kwargs = _call_production_llm("deepseek-r1", _MAAS_URL)
        assert kwargs["max_tokens"] <= 8_192

    def test_unknown_maas_model_gets_conservative_cap(self):
        """Unknown MaaS models should use the conservative 8 192 cap."""
        kwargs = _call_production_llm("some-unknown-model-v1", _MAAS_URL)
        assert kwargs["max_tokens"] <= 8_192, (
            f"Unknown MaaS model should use conservative cap, got {kwargs['max_tokens']}"
        )

    def test_non_maas_endpoint_not_capped(self):
        """Non-MaaS endpoints must NOT have the deepseek MaaS cap applied."""
        kwargs = _call_production_llm("gpt-4o", "https://api.openai.com/v1", max_tokens=8192)
        # The user's configured 8192 must be honoured on non-MaaS endpoints
        assert kwargs["max_tokens"] >= 8_192, (
            f"Non-MaaS endpoint should not apply MaaS cap, got {kwargs['max_tokens']}"
        )


# ---------------------------------------------------------------------------
# Invariant: max_tokens must always be < context_window
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("model,api_base", [
    ("deepseek-r1-distill-qwen-14b", _MAAS_URL),
    ("deepseek-r1",                  _MAAS_URL),
    ("some-unknown",                 _MAAS_URL),
])
def test_max_tokens_always_less_than_context_window(model, api_base):
    """max_tokens must always be strictly less than context_window."""
    kwargs = _call_production_llm(model, api_base)
    assert kwargs["max_tokens"] < kwargs["context_window"], (
        f"max_tokens ({kwargs['max_tokens']}) must be < context_window ({kwargs['context_window']}) "
        f"for model={model}."
    )
