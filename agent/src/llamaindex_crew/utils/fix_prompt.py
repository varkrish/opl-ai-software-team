"""Structured prompts for JIRA bug / fix jobs."""
from __future__ import annotations

from typing import Optional


def build_fix_vision(
    *,
    summary: str,
    description: str = "",
    issue_key: str = "",
    expected: str = "",
    actual: str = "",
) -> str:
    """Build a fix-job vision / refinement prompt from JIRA bug fields."""
    key_line = f"Fix JIRA {issue_key}: {summary}" if issue_key else f"Fix: {summary}"
    parts = [key_line, ""]
    if description.strip():
        parts.extend(["Steps to reproduce / description:", description.strip(), ""])
    if expected.strip():
        parts.extend(["Expected:", expected.strip(), ""])
    if actual.strip():
        parts.extend(["Actual:", actual.strip(), ""])
    parts.extend([
        "Constraints:",
        "- Minimal, targeted change; do not refactor unrelated code",
        "- Preserve existing patterns in tech_stack.md and project conventions",
        "- Update or add tests that cover the fix",
    ])
    return "\n".join(parts)


def fix_commit_message(issue_key: str, summary: str) -> str:
    """Conventional commit message for a bug fix."""
    key = (issue_key or "BUG").strip()
    short = (summary or "fix issue").strip()[:72]
    return f"fix({key}): {short}"
