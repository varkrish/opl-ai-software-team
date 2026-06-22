"""Helpers for dev-phase prompt sizing and validation retry policy."""
from __future__ import annotations

import re
from pathlib import Path

# File paths that typically need more context and complete single-shot output.
_LARGE_FILE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"doctype/", re.I),
    re.compile(r"hooks\.py$", re.I),
    re.compile(r"Service\.java$"),
    re.compile(r"Controller\.java$"),
    re.compile(r"Repository\.java$"),
    re.compile(r"migration", re.I),
    re.compile(r"routes?/", re.I),
    re.compile(r"models?/", re.I),
    re.compile(r"pages?/", re.I),
    re.compile(r"App\.(tsx|jsx)$"),
    re.compile(r"schema\.(py|ts|sql)$"),
)

# Issues that warrant an LLM retry (syntax / missing output). Style warnings are skipped
# when simple_mode_retry_critical_only is enabled.
_CRITICAL_ISSUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"SyntaxError", re.I),
    re.compile(r"IndentationError", re.I),
    re.compile(r"unexpected indent", re.I),
    re.compile(r"invalid syntax", re.I),
    re.compile(r"parse error", re.I),
    re.compile(r"unclosed", re.I),
    re.compile(r"line continuation", re.I),
    re.compile(r"cannot import", re.I),
    re.compile(r"import.*could not be resolved", re.I),
    re.compile(r"undefined name", re.I),
    re.compile(r"was not created", re.I),
)


def is_likely_large_file(file_path: str) -> bool:
    """Heuristic: target file likely needs a large, complete implementation."""
    if not file_path:
        return False
    norm = file_path.replace("\\", "/")
    if any(p.search(norm) for p in _LARGE_FILE_PATTERNS):
        return True
    # Long multi-segment paths (deep module trees) often carry more boilerplate.
    return len(Path(norm).parts) >= 4


def is_critical_validation_issue(issue: str) -> bool:
    """Return True if *issue* should trigger a regeneration retry."""
    text = (issue or "").strip()
    if not text:
        return False
    return any(p.search(text) for p in _CRITICAL_ISSUE_PATTERNS)


def filter_retry_issues(issues: list[str], *, critical_only: bool) -> list[str]:
    """Return the subset of validation issues that should trigger a retry."""
    if not critical_only:
        return list(issues)
    return [i for i in issues if is_critical_validation_issue(i)]


def trim_tech_stack_for_prompt(tech_stack: str, max_chars: int) -> str:
    """Trim tech stack text while preserving the file-structure tree when possible."""
    text = (tech_stack or "").strip()
    if len(text) <= max_chars:
        return text

    structure_match = re.search(
        r"(##\s*File Structure[\s\S]*?```[\s\S]*?```)",
        text,
        re.IGNORECASE,
    )
    if structure_match:
        structure_block = structure_match.group(1)
        intro_budget = max(500, max_chars - len(structure_block) - 80)
        intro = text[:intro_budget].rstrip()
        combined = f"{intro}\n\n{structure_block}"
        if len(combined) <= max_chars:
            return combined + "\n\n[... tech stack narrative truncated ...]"
        # Structure alone is too large — keep full structure if it fits, else truncate tail.
        if len(structure_block) <= max_chars:
            return structure_block + "\n\n[... tech stack narrative truncated ...]"

    return text[:max_chars] + "\n\n[... tech stack truncated ...]"


def trim_user_stories_for_prompt(user_stories: str, max_chars: int) -> str:
    """Trim user stories, keeping the header and first sections."""
    text = (user_stories or "").strip()
    if len(text) <= max_chars:
        return text
    head = text[: max_chars - 60].rstrip()
    return head + "\n\n[... user stories truncated ...]"
