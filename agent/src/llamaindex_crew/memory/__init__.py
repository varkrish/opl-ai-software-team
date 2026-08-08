"""
Cross-job context memory plane (P3 — historical knowledge).

Job outcomes, Jira context, and reference-doc summaries are written here at
lifecycle hooks and recalled at solutioning time, so a new job on a domain
starts with what past jobs learned instead of re-deriving it.

Everything in this package is fail-open: if the memory plane is disabled,
the client library is absent, or the server is unreachable, callers get a
disabled no-op and jobs run exactly as they do without it.
"""
from .scope import MemoryScope, resolve_domain, resolve_framework, resolve_scope, slugify
from .context_memory import ContextMemory, get_context_memory
from .corrections import Correction, collect_corrections, render_correction
from .summaries import (
    build_jira_context_summary,
    build_jira_epic_summary,
    collect_job_outcome_signals,
    summarize_job_outcome,
    summarize_reference_doc,
)

__all__ = [
    "ContextMemory",
    "Correction",
    "MemoryScope",
    "collect_corrections",
    "render_correction",
    "build_jira_context_summary",
    "build_jira_epic_summary",
    "collect_job_outcome_signals",
    "get_context_memory",
    "resolve_domain",
    "resolve_framework",
    "resolve_scope",
    "slugify",
    "summarize_job_outcome",
    "summarize_reference_doc",
]
