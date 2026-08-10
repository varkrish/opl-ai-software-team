"""
Scope resolution for the context memory plane.

MemMachine 0.3.x has only two levels of first-class hierarchy — ``org_id`` and
``project_id`` — so the four-level scoping we want (customer / framework /
domain / agent) maps like this:

    org_id     = customer          → first-class, hard isolation boundary
    project_id = framework         → first-class
    domain     → instance metadata (``group_id``), which the client turns into
                 an automatic search filter via ``get_default_filter_dict()``
    agent      → per-write ``producer`` field

Only scope keys belong in instance metadata, because the client auto-filters
every search on them. Per-write facts (``job_id``, ``type``, status) are passed
to ``add(metadata=...)`` instead: they get stored on the episode but do not
narrow later searches. Putting ``job_id`` in instance metadata would scope every
recall to a single job and silently defeat the whole plane.

Slugs are normalised aggressively because scope fragmentation is the main
failure mode here: "Frappe 15" and "frappe-15" resolving to different projects
means two halves of the same history that can never find each other.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_MAX_SLUG_LEN = 64

# Frameworks we can name confidently from a stack manifest or tech_stack.md.
# Ordered most-specific-first so "spring-boot" wins over "spring".
_FRAMEWORK_MARKERS: tuple[tuple[str, str], ...] = (
    ("frappe", "frappe"),
    ("erpnext", "frappe"),
    ("spring boot", "spring-boot"),
    ("springboot", "spring-boot"),
    ("quarkus", "quarkus"),
    ("django", "django"),
    ("fastapi", "fastapi"),
    ("flask", "flask"),
    ("express", "express"),
    ("nestjs", "nestjs"),
    ("next.js", "nextjs"),
    ("nextjs", "nextjs"),
    ("react", "react"),
    ("angular", "angular"),
    ("vue", "vue"),
    ("dotnet", "dotnet"),
    (".net", "dotnet"),
    ("rails", "rails"),
    ("laravel", "laravel"),
    ("go ", "go"),
    ("golang", "go"),
)


def slugify(value: Any, fallback: str = "") -> str:
    """Normalise an arbitrary label into a stable scope slug."""
    text = str(value or "").strip().lower()
    slug = _SLUG_STRIP.sub("-", text).strip("-")
    if len(slug) > _MAX_SLUG_LEN:
        slug = slug[:_MAX_SLUG_LEN].rstrip("-")
    return slug or fallback


@dataclass(frozen=True)
class MemoryScope:
    """Resolved scope for one job's memory reads and writes."""

    org_id: str
    project_id: str
    domain: str
    agent_id: Optional[str] = None
    extra: Dict[str, str] = field(default_factory=dict)

    def instance_metadata(self) -> Dict[str, str]:
        """
        Metadata attached to the ``Memory`` instance.

        Every key here becomes an automatic search filter, so this must contain
        only scope dimensions — never per-job or per-episode facts.
        """
        meta = {"group_id": self.domain}
        meta.update({k: str(v) for k, v in self.extra.items() if v})
        return meta

    def describe(self) -> str:
        return f"org={self.org_id} project={self.project_id} domain={self.domain}"


def _framework_from_text(text: str) -> str:
    haystack = f" {str(text or '').lower()} "
    for marker, slug in _FRAMEWORK_MARKERS:
        if marker in haystack:
            return slug
    return ""


def _read_stack_manifest(workspace_path: Optional[Path]) -> Optional[Dict[str, Any]]:
    if not workspace_path:
        return None
    path = Path(workspace_path) / "stack_manifest.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def resolve_framework(
    metadata: Optional[Dict[str, Any]] = None,
    workspace_path: Optional[Path] = None,
    *,
    fallback: str = "unknown-framework",
) -> str:
    """
    Resolve the framework (``project_id``) for a job.

    Order: explicit job metadata → locked stack manifest → tech_stack.md text
    → fallback. The first two are authoritative; the text scan is a guess.
    """
    metadata = metadata or {}

    explicit = metadata.get("framework") or metadata.get("target_framework")
    if explicit:
        return slugify(explicit, fallback)

    manifest = _read_stack_manifest(workspace_path)
    if manifest:
        chosen = manifest.get("chosen_stack") or []
        if isinstance(chosen, list):
            for entry in chosen:
                slug = _framework_from_text(str(entry))
                if slug:
                    return slug
            if chosen:
                return slugify(chosen[0], fallback)

    if workspace_path:
        tech_stack = Path(workspace_path) / "tech_stack.md"
        if tech_stack.exists():
            try:
                slug = _framework_from_text(tech_stack.read_text(encoding="utf-8")[:8000])
            except (OSError, UnicodeDecodeError):
                slug = ""
            if slug:
                return slug

    return fallback


def resolve_domain(
    metadata: Optional[Dict[str, Any]] = None,
    vision: str = "",
    *,
    fallback: str = "general",
) -> str:
    """
    Resolve the domain (``group_id``) for a job.

    Order: explicit job metadata (authoritative once persisted) → Jira project
    key → fallback. We deliberately do **not** guess a domain from vision text:
    an unstable guess fragments recall worse than a single shared bucket does.
    Persist the result to ``jobs.metadata["domain"]`` so it never drifts.
    """
    metadata = metadata or {}

    explicit = metadata.get("domain") or metadata.get("group_id")
    if explicit:
        return slugify(explicit, fallback)

    project_key = metadata.get("jira_project_key")
    if not project_key:
        issue_key = str(metadata.get("jira_issue_key") or metadata.get("jira_epic_key") or "")
        if "-" in issue_key:
            project_key = issue_key.rsplit("-", 1)[0]
    if project_key:
        return slugify(project_key, fallback)

    return fallback


def resolve_scope(
    job: Optional[Dict[str, Any]] = None,
    *,
    workspace_path: Optional[Path] = None,
    agent_id: Optional[str] = None,
    default_org_id: str = "default",
    default_project_id: str = "unknown-framework",
    metadata: Optional[Dict[str, Any]] = None,
) -> MemoryScope:
    """
    Build a :class:`MemoryScope` from a job row.

    ``team_id`` wins over ``owner_id`` for the customer boundary so that
    teammates share one memory pool rather than each building a private one.

    That intent did not survive contact with the data: nothing populates
    ``team_id`` — it is an optional field on job creation and null on every live
    job — so the boundary collapsed to ``owner_id`` and each developer built a
    private pool. An org of a hundred developers would produce a hundred pools
    that never see each other's approved plans, which is the opposite of the
    point.

    ``CREW_ORG_ID`` sits between the two: set it and the whole deployment shares
    one pool, which is what a single-org install wants. Left unset, behaviour is
    unchanged, so this cannot silently merge pools that were meant to be
    separate.

    Sharing widens only the org. ``project_id`` (the framework) and ``domain``
    still narrow retrieval, so a Java plan is not offered to a Python job.
    """
    job = job or {}
    if metadata is None:
        metadata = job.get("metadata") or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}

    org_raw = (
        job.get("team_id")
        or os.getenv("CREW_ORG_ID")
        or job.get("owner_id")
        or metadata.get("customer_id")
    )
    org_id = slugify(org_raw, default_org_id)

    project_id = resolve_framework(
        metadata, workspace_path, fallback=slugify(default_project_id, "unknown-framework")
    )
    domain = resolve_domain(metadata, job.get("vision") or "")

    return MemoryScope(
        org_id=org_id,
        project_id=project_id,
        domain=domain,
        agent_id=slugify(agent_id) or None if agent_id else None,
    )
