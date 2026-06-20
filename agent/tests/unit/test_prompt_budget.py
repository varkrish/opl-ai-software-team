"""
Unit tests for prompt_budget.py — context-aware prompt trimming.

These tests document and protect the contract that NO prompt ever overflows
the model's context window.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from llamaindex_crew.utils.prompt_budget import (
    PromptBudget,
    estimate_tokens,
    tokens_to_chars,
    trim_text,
)


# ---------------------------------------------------------------------------
# estimate_tokens
# ---------------------------------------------------------------------------

class TestEstimateTokens:
    def test_empty_string(self):
        assert estimate_tokens("") == 1  # floor

    def test_approx_four_chars_per_token(self):
        text = "a" * 400
        assert estimate_tokens(text) == 100

    def test_longer_text(self):
        text = "hello world " * 100   # 1200 chars → ~300 tokens
        assert 250 <= estimate_tokens(text) <= 350


# ---------------------------------------------------------------------------
# trim_text
# ---------------------------------------------------------------------------

class TestTrimText:
    def test_short_text_unchanged(self):
        t = "short text"
        assert trim_text(t, 1000) == t

    def test_long_text_trimmed(self):
        t = "x" * 10_000
        result = trim_text(t, 100)
        assert estimate_tokens(result) <= 110   # within small margin of suffix
        assert "truncated" in result

    def test_suffix_appended(self):
        t = "y" * 5_000
        result = trim_text(t, 50)
        assert result.endswith("[... truncated to fit context window ...]")


# ---------------------------------------------------------------------------
# PromptBudget.from_context
# ---------------------------------------------------------------------------

class TestPromptBudgetFromContext:
    def test_budget_leaves_input_headroom(self):
        budget = PromptBudget.from_context(context_window=16_384, max_tokens=6_144)
        # budget = 16384 - 6144 - 600 = 9640
        assert budget.input_token_budget == 9_640

    def test_budget_floor(self):
        # Even with a very large max_tokens, the budget should never go below 1000
        budget = PromptBudget.from_context(context_window=16_384, max_tokens=16_000)
        assert budget.input_token_budget >= 1_000

    def test_fits_respects_budget(self):
        budget = PromptBudget.from_context(16_384, 6_144)
        short = "a" * 100
        long_ = "b" * 1_000_000
        assert budget.fits(short)
        assert not budget.fits(long_)


# ---------------------------------------------------------------------------
# PromptBudget.fit — section trimming
# ---------------------------------------------------------------------------

class TestPromptBudgetFit:
    def _make_budget(self) -> PromptBudget:
        return PromptBudget.from_context(context_window=16_384, max_tokens=6_144)

    def test_no_trim_needed(self):
        budget = self._make_budget()
        sections = {"a": "hello " * 10, "b": "world " * 10}
        result = budget.fit(sections)
        assert result["a"] == sections["a"]
        assert result["b"] == sections["b"]

    def test_large_sections_trimmed(self):
        budget = self._make_budget()
        # Each section is 20k tokens — way over the ~9640 budget
        big = "x" * 80_000
        sections = {"design_spec": big, "skill_context": big, "vision": "short vision"}
        result = budget.fit(sections)
        total = sum(estimate_tokens(v) for v in result.values())
        assert total <= budget.input_token_budget + 50  # within rounding margin

    def test_combined_total_fits_budget(self):
        budget = PromptBudget.from_context(16_384, 6_144)
        sections = {
            "vision":        "Build a task management app. " * 50,
            "design_spec":   "Entity-relationship model... " * 300,
            "context_digest": "Previous story: created models. " * 200,
            "skill_context": "Framework reference: FastAPI... " * 400,
        }
        result = budget.fit(
            sections,
            fixed_overhead_chars=800,
            priority=["vision", "design_spec", "context_digest", "skill_context"],
        )
        total = sum(estimate_tokens(v) for v in result.values())
        assert total <= budget.input_token_budget + 100

    def test_priority_protects_earlier_sections(self):
        """Sections listed first in priority should retain more content."""
        budget = PromptBudget.from_context(8_000, 4_000)
        big = "z" * 20_000
        sections = {"high_priority": big, "low_priority": big}
        result = budget.fit(
            sections,
            priority=["high_priority", "low_priority"],
        )
        # high_priority should have at least as much content as low_priority
        # (actually equal here since same size, but both trimmed)
        assert len(result["high_priority"]) >= len(result["low_priority"]) - 100

    def test_empty_sections_returned_unchanged(self):
        budget = self._make_budget()
        assert budget.fit({}) == {}

    def test_none_values_handled(self):
        budget = self._make_budget()
        sections = {"a": "", "b": "hello"}
        result = budget.fit(sections)
        assert result["a"] == ""


# ---------------------------------------------------------------------------
# _trim_existing_files (via task_manager module)
# ---------------------------------------------------------------------------

class TestTrimExistingFiles:
    def test_import(self):
        from llamaindex_crew.orchestrator.task_manager import _trim_existing_files
        assert callable(_trim_existing_files)

    def test_small_files_not_trimmed(self):
        from llamaindex_crew.orchestrator.task_manager import _trim_existing_files
        files = {"a.py": "print('hello')", "b.py": "x = 1"}
        result = _trim_existing_files(files, context_window=16_384, max_tokens=6_144)
        assert result == files

    def test_large_files_trimmed(self):
        from llamaindex_crew.orchestrator.task_manager import _trim_existing_files
        big_content = "x = 1\n" * 5_000   # ~30k chars → ~7500 tokens
        files = {f"file{i}.py": big_content for i in range(5)}
        result = _trim_existing_files(files, context_window=16_384, max_tokens=6_144)
        total_tokens = sum(estimate_tokens(v) for v in result.values())
        budget = PromptBudget.from_context(16_384, 6_144)
        # Trimmed total must fit within input budget
        assert total_tokens <= budget.input_token_budget + 200

    def test_none_context_window_uses_default(self):
        from llamaindex_crew.orchestrator.task_manager import _trim_existing_files
        files = {"a.py": "code " * 10}
        result = _trim_existing_files(files, context_window=None, max_tokens=None)
        assert "a.py" in result   # no crash


# ---------------------------------------------------------------------------
# _trim_payload_for_context (from llm_config)
# ---------------------------------------------------------------------------

class TestTrimPayloadForContext:
    def test_import(self):
        from llamaindex_crew.utils.llm_config import _trim_payload_for_context
        assert callable(_trim_payload_for_context)

    def test_trims_last_user_message(self):
        from llamaindex_crew.utils.llm_config import _trim_payload_for_context
        big_content = "word " * 5_000
        payload = {
            "model": "test",
            "messages": [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": big_content},
            ]
        }
        result = _trim_payload_for_context(payload, trim_fraction=0.25)
        trimmed_content = result["messages"][1]["content"]
        assert len(trimmed_content) < len(big_content)
        assert "trimmed" in trimmed_content

    def test_preserves_system_message(self):
        from llamaindex_crew.utils.llm_config import _trim_payload_for_context
        payload = {
            "model": "test",
            "messages": [
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": "user " * 5_000},
            ]
        }
        result = _trim_payload_for_context(payload, trim_fraction=0.25)
        assert result["messages"][0]["content"] == "system prompt"

    def test_no_user_message_returns_original(self):
        from llamaindex_crew.utils.llm_config import _trim_payload_for_context
        payload = {"model": "test", "messages": [{"role": "system", "content": "sys"}]}
        result = _trim_payload_for_context(payload)
        assert result is payload

    def test_empty_messages_returns_original(self):
        from llamaindex_crew.utils.llm_config import _trim_payload_for_context
        payload = {"model": "test", "messages": []}
        result = _trim_payload_for_context(payload)
        assert result is payload

    def test_original_payload_not_mutated(self):
        from llamaindex_crew.utils.llm_config import _trim_payload_for_context
        content = "original " * 5_000
        payload = {
            "model": "test",
            "messages": [{"role": "user", "content": content}]
        }
        _trim_payload_for_context(payload, trim_fraction=0.25)
        # Original must be unchanged (deep copy used)
        assert payload["messages"][0]["content"] == content

    def test_trim_fraction_respected(self):
        from llamaindex_crew.utils.llm_config import _trim_payload_for_context
        content = "a" * 10_000
        payload = {"model": "test", "messages": [{"role": "user", "content": content}]}
        result = _trim_payload_for_context(payload, trim_fraction=0.50)
        kept = result["messages"][0]["content"]
        # Should keep ~50% of original
        assert len(kept) < len(content) * 0.6
