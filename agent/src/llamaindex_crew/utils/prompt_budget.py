"""
Prompt budget management — token-aware prompt construction.

LLMs have hard context limits: context_window = input_tokens + output_tokens.
When a prompt exceeds (context_window - max_tokens), the API returns 400.

This module provides utilities to:
  1. Estimate token count from text (fast, no external tokeniser needed).
  2. Trim named "sections" of a prompt proportionally so the total fits within
     a given token budget.
  3. Build a budget from a (context_window, max_tokens) pair.

Usage
-----
    from llamaindex_crew.utils.prompt_budget import PromptBudget

    budget = PromptBudget.from_llm(llm)          # input headroom
    sections = {
        "design_spec":    design_spec,
        "context_digest": context_digest or "",
        "skill_context":  skill_context or "",
    }
    fixed_overhead = len(task_prompt_template)    # chars for the static part
    trimmed = budget.fit(sections, fixed_overhead_chars=fixed_overhead)
    prompt = task_prompt_template.format(**trimmed)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Empirical approximation: average English/code token ≈ 4 characters.
# tiktoken would be more accurate but adds a heavyweight dependency and requires
# network access on first use.  4 chars/token is well-established and conservative
# (real code tokens are often longer, so we slightly overestimate — safe direction).
_CHARS_PER_TOKEN: float = 4.0

# Reserve this many tokens for the system prompt + ReAct scaffolding overhead.
_SYSTEM_OVERHEAD_TOKENS: int = 600


def estimate_tokens(text: str) -> int:
    """Estimate the number of tokens in *text* (no tokeniser required)."""
    return max(1, int(len(text) / _CHARS_PER_TOKEN))


def tokens_to_chars(tokens: int) -> int:
    """Convert a token budget to an approximate character budget."""
    return int(tokens * _CHARS_PER_TOKEN)


def trim_text(text: str, max_tokens: int, suffix: str = "\n[... truncated to fit context window ...]") -> str:
    """Trim *text* to *max_tokens* (approximate), appending *suffix* if trimmed."""
    budget_chars = tokens_to_chars(max_tokens) - len(suffix)
    if len(text) <= budget_chars + len(suffix):
        return text
    logger.debug("trim_text: %.0f → %.0f tokens", estimate_tokens(text), max_tokens)
    return text[:max(0, budget_chars)] + suffix


@dataclass
class PromptBudget:
    """Encapsulates the available input-token headroom for a single LLM call."""

    input_token_budget: int   # total tokens available for the user prompt

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------

    @classmethod
    def from_llm(cls, llm) -> "PromptBudget":
        """
        Build a PromptBudget from a LlamaIndex LLM object.

        Reads ``context_window`` and ``max_tokens`` from *llm*.  Falls back to
        conservative defaults if the attributes are absent.
        """
        context_window: int = getattr(llm, "context_window", 16_384)
        max_tokens: int = getattr(llm, "max_tokens", 4_096)
        return cls.from_context(context_window, max_tokens)

    @classmethod
    def from_context(cls, context_window: int, max_tokens: int) -> "PromptBudget":
        """Build a PromptBudget from explicit context_window / max_tokens values."""
        budget = context_window - max_tokens - _SYSTEM_OVERHEAD_TOKENS
        budget = max(budget, 1_000)   # always leave a floor so agents can still run
        return cls(input_token_budget=budget)

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def fits(self, text: str) -> bool:
        """Return True if *text* fits within the input budget."""
        return estimate_tokens(text) <= self.input_token_budget

    def fit(
        self,
        sections: Dict[str, str],
        fixed_overhead_chars: int = 0,
        *,
        priority: Optional[list] = None,
    ) -> Dict[str, str]:
        """
        Trim variable *sections* so the combined prompt fits within the budget.

        Parameters
        ----------
        sections:
            Mapping of section name → content.  All sections may be trimmed.
        fixed_overhead_chars:
            Characters consumed by static/template text that is NOT in
            *sections* (e.g. the task prompt template, headings, separators).
            Subtracted from the budget before distributing among sections.
        priority:
            Optional ordered list of section names.  Sections listed later are
            trimmed first (i.e. earlier = higher priority = protected longer).
            Sections not in the list are treated as equal lowest priority and
            trimmed after listed sections.

        Returns
        -------
        A new dict with the same keys but content trimmed to fit.
        """
        if not sections:
            return {}

        fixed_overhead_tokens = int(fixed_overhead_chars / _CHARS_PER_TOKEN)
        available = self.input_token_budget - fixed_overhead_tokens
        available = max(available, 500)   # absolute floor

        current_tokens = {k: estimate_tokens(v) for k, v in sections.items()}
        total_tokens = sum(current_tokens.values())

        if total_tokens <= available:
            return dict(sections)   # already fits, no trimming needed

        logger.info(
            "PromptBudget.fit: total=%d tokens > budget=%d. Trimming sections.",
            total_tokens, available,
        )

        # Determine trim order: listed sections are protected (trimmed last).
        if priority is None:
            trim_order = list(sections.keys())
        else:
            in_priority = [k for k in priority if k in sections]
            not_in_priority = [k for k in sections if k not in priority]
            # trim not-in-priority first, then priority in reverse order (last = lowest priority)
            trim_order = not_in_priority + list(reversed(in_priority))

        result = {k: v for k, v in sections.items()}
        remaining_budget = available

        # First pass: assign budget proportionally per section.
        # Sections that are already small keep their full content.
        # Sections that are large are capped proportionally.
        for key in trim_order:
            own_share = max(
                200,                                # minimum floor per section
                int(available * current_tokens[key] / max(total_tokens, 1)),
            )
            if current_tokens[key] > own_share:
                result[key] = trim_text(result[key], own_share)
                logger.debug(
                    "  section '%s': %d → %d tokens (budget share %d)",
                    key, current_tokens[key], estimate_tokens(result[key]), own_share,
                )

        return result

    def trim_single(self, text: str, label: str = "text") -> str:
        """Trim a single block of text to fit the full input budget."""
        if self.fits(text):
            return text
        logger.info(
            "PromptBudget.trim_single '%s': %d → %d tokens",
            label, estimate_tokens(text), self.input_token_budget,
        )
        return trim_text(text, self.input_token_budget)
