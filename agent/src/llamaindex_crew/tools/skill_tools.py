"""
SkillQueryTool — FunctionTool factory for querying the skills service.

Also provides ``prefetch_skills()`` for programmatic skill injection
into agent prompts (Designer, Tech Architect) so downstream agents
inherit framework knowledge through design_spec.md / tech_stack.md.

Usage:
    tool = SkillQueryTool(service_url="http://skills:8090", default_tags=["python"])
    # Returns a FunctionTool that agents can call to search skill docs.

    context = prefetch_skills("Build a Frappe invoicing app")
    # Returns formatted skill sections for prompt injection.
"""
import json
import logging
from pathlib import Path
from typing import List, Optional

import httpx
import os
from llama_index.core.tools import FunctionTool

logger = logging.getLogger(__name__)

# Client-side backstop matching skills-service's own SKILLS_MIN_SCORE default
# (see skills-service/src/indexer.py for the calibration methodology). Results
# without a "score" key (older service versions) are treated as unfiltered/
# legacy and kept — the service-side threshold is the primary gate.
MIN_SKILL_SCORE = float(os.environ.get("SKILLS_MIN_SCORE", "0.68"))

# Skills that conflict with forbidden application-platform tiers on a client lock.
_PLATFORM_SKILL_MARKERS = (
    "frappe", "django", "fastapi", "flask", "spring", "rails", "laravel",
    "erpnext", "doctype", "mariadb", "postgres", "mongodb",
)

# Exclusive framework families: a skill named/tagged for one must not inject
# when chosen_stack locks a different family (e.g. Frappe skills on a Go job).
_EXCLUSIVE_SKILL_FAMILIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("frappe", ("frappe", "erpnext", "doctype")),
    ("spring", ("spring-boot", "spring boot", "springframework", "springboot")),
    ("django", ("django",)),
    ("fastapi", ("fastapi",)),
    ("flask", ("flask",)),
    ("rails", ("rails", "ruby on rails")),
    ("laravel", ("laravel",)),
    ("react", ("react", "reactjs")),
    ("angular", ("angular",)),
    ("vue", ("vue", "vuejs")),
    ("golang", ("golang", "gin-gonic")),
)


def _passes_relevance(result: dict) -> bool:
    score = result.get("score")
    return score is None or score >= MIN_SKILL_SCORE


def _load_stack_manifest(workspace_path: Optional[Path]) -> Optional[dict]:
    if not workspace_path:
        return None
    try:
        from ..workflows.solutioning_loop import read_stack_manifest
        return read_stack_manifest(Path(workspace_path))
    except Exception:
        return None


def _chosen_matches_family(chosen_blob: str, family: str, markers: tuple[str, ...]) -> bool:
    if family in chosen_blob:
        return True
    if any(m in chosen_blob for m in markers):
        return True
    # Spring Boot jobs often lock "spring boot"; plain "java" must NOT match spring.
    if family == "golang" and ("go" == chosen_blob.strip() or " go " in f" {chosen_blob} "):
        return True
    return False


def _skill_exclusive_family_conflict(skill_name: str, content: str, chosen: list[str]) -> bool:
    """True when skill belongs to a framework family absent from chosen_stack."""
    if not chosen:
        return False
    chosen_blob = " ".join(chosen).lower()
    name_l = (skill_name or "").lower()
    # Prefer skill name — content can mention many stacks in comparisons.
    for family, markers in _EXCLUSIVE_SKILL_FAMILIES:
        skill_is_family = name_l.startswith(family) or any(m in name_l for m in markers)
        if not skill_is_family and family == "spring":
            skill_is_family = "spring" in name_l
        if not skill_is_family:
            continue
        if not _chosen_matches_family(chosen_blob, family, markers):
            return True
    return False


def _skill_conflicts_with_manifest(skill_name: str, content: str, manifest: dict) -> bool:
    """Drop skills that fight the locked stack_manifest."""
    from ..utils.vision_stack_analysis import _effective_forbidden_tiers

    chosen = [s.lower() for s in (manifest.get("chosen_stack") or [])]
    if _skill_exclusive_family_conflict(skill_name, content, chosen):
        return True

    forbidden = set(
        _effective_forbidden_tiers(
            [t.lower() for t in (manifest.get("forbidden_tiers") or [])],
            chosen,
        )
    )
    if not forbidden.intersection({"application_server", "database", "cms_platform"}):
        return False
    chosen_set = set(chosen)
    blob = f"{skill_name} {content}".lower()
    for marker in _PLATFORM_SKILL_MARKERS:
        if marker in blob and marker not in chosen_set:
            name_l = (skill_name or "").lower()
            if any(m in name_l for m in _PLATFORM_SKILL_MARKERS):
                return True
            if marker in ("frappe", "django", "fastapi", "flask", "spring", "rails",
                          "laravel", "erpnext", "doctype"):
                return True
    return False


def _queries_for_role(
    vision: str,
    role: str,
    extra_queries: Optional[List[str]],
    manifest: Optional[dict],
) -> List[str]:
    """Build prefetch queries; prefer stack_manifest.skills_query when locked."""
    seed = (vision or "")[:200]
    if manifest and (manifest.get("skills_query") or "").strip():
        seed = manifest["skills_query"].strip()
    elif manifest and manifest.get("chosen_stack"):
        seed = " ".join(str(s) for s in manifest["chosen_stack"])

    if role == "designer":
        queries = [
            f"design patterns UI architecture conventions {seed}",
            f"component architecture implementation patterns {seed}",
        ]
    elif role == "tech_architect":
        queries = [
            f"folder structure scaffold file tree {seed}",
            f"coding patterns implementation architecture {seed}",
        ]
    else:
        queries = [
            f"architecture conventions patterns {seed}",
            f"folder structure implementation {seed}",
        ]

    if extra_queries:
        queries.extend(extra_queries)
    return queries

def _record_skills_used(job_id: str, skill_names: List[str]):
    try:
        from crew_studio.job_database import JobDatabase
        db_path = os.getenv("JOB_DB_PATH")
        if db_path and job_id:
            db = JobDatabase(Path(db_path))
            db.add_skills_used(job_id, skill_names)
    except Exception as e:
        logger.warning("Failed to record skills used: %s", e)


def resolve_skills_service_url(config=None) -> Optional[str]:
    """Resolve skills HTTP base URL from config YAML, then ``SKILLS_SERVICE_URL`` env.

    Dev compose sets ``SKILLS_SERVICE_URL`` on the backend even when
    ``~/.crew-ai/config.yaml`` omits ``skills.service_url``. Prefetch and
    agent tools must honor the env fallback (same as asgi_app / DevOps).
    """
    url = None
    if config is not None:
        skills = getattr(config, "skills", None)
        url = getattr(skills, "service_url", None) if skills else None
    else:
        try:
            from ..config import ConfigLoader

            loaded = ConfigLoader.load()
            skills = getattr(loaded, "skills", None)
            url = getattr(skills, "service_url", None) if skills else None
        except Exception:
            url = None
    if not url:
        url = (os.environ.get("SKILLS_SERVICE_URL") or "").strip() or None
    return url.rstrip("/") if url else None


def _write_prefetch_debug(
    workspace_path: Optional[Path],
    role: str,
    *,
    entries: Optional[list] = None,
    meta: Optional[dict] = None,
) -> None:
    """Persist skill_prefetch.json so missing skills are visible in the workspace."""
    if not workspace_path:
        return
    try:
        prefetch_file = Path(workspace_path) / "skill_prefetch.json"
        existing: dict = {}
        if prefetch_file.exists():
            existing = json.loads(prefetch_file.read_text(encoding="utf-8"))
            if not isinstance(existing, dict):
                existing = {}
        if entries is not None:
            existing[role] = entries
        if meta:
            meta_block = existing.get("_meta")
            if not isinstance(meta_block, dict):
                meta_block = {}
            meta_block[role] = meta
            existing["_meta"] = meta_block
        prefetch_file.write_text(
            json.dumps(existing, indent=2, default=str), encoding="utf-8",
        )
    except Exception:
        logger.debug("skill_prefetch.json write failed", exc_info=True)


def SkillQueryTool(
    service_url: str,
    default_tags: Optional[List[str]] = None,
    job_id: Optional[str] = None,
) -> FunctionTool:
    """Create a FunctionTool that queries the skills service over HTTP."""

    def query_skills(
        query: str,
        tags: Optional[List[str]] = None,
        top_k: int = 3,
    ) -> str:
        effective_tags = tags or default_tags
        payload: dict = {"query": query, "top_k": top_k}
        if effective_tags:
            payload["tags"] = effective_tags

        logger.info("Querying skills service: query=%r tags=%s top_k=%d", query, effective_tags, top_k)
        try:
            resp = httpx.post(f"{service_url}/query", json=payload, timeout=30)
            resp.raise_for_status()
            raw_results = resp.json().get("results", [])
            results = [r for r in raw_results if _passes_relevance(r)]
            if len(results) < len(raw_results):
                logger.info(
                    "Skills query: dropped %d low-relevance result(s) below threshold %.2f",
                    len(raw_results) - len(results), MIN_SKILL_SCORE,
                )
            if not results:
                logger.info("Skills query returned 0 relevant results")
                return "No matching skills found."
            parts = [f"[{r['skill_name']}] {r['content']}" for r in results]
            logger.info("Skills query returned %d relevant results", len(results))
            if job_id:
                _record_skills_used(job_id, [r['skill_name'] for r in results])
            return "\n---\n".join(parts)
        except Exception:
            logger.warning("Skills service unavailable", exc_info=True)
            return ""

    return FunctionTool.from_defaults(
        fn=query_skills,
        name="skill_query",
        description=(
            "Search project skills and coding guidelines by meaning. "
            "Returns relevant skill content from indexed SKILL.md files. "
            "Optionally filter by tags (e.g. ['python', 'frappe'])."
        ),
    )


def prefetch_skills(
    vision: str,
    role: str = "general",
    extra_queries: Optional[List[str]] = None,
    top_k: int = 3,
    workspace_path: Optional[Path] = None,
) -> str:
    """Programmatically fetch skills and return formatted context for prompt injection.

    This is the core mechanism that makes Designer and Tech Architect outputs
    implementation-specific.  By injecting real skill content into their task
    prompts, we guarantee that design_spec.md and tech_stack.md contain the
    framework's actual conventions — so downstream agents (developer, frontend,
    reviewer) follow the right patterns without needing their own skill calls.

    Args:
        vision:  Project vision text used to derive semantic queries.
        role:    ``"designer"`` or ``"tech_architect"`` — controls default
                 query set.  Anything else uses a generic set.
        extra_queries:  Additional queries appended to the defaults.
        top_k:   Results per query.
        workspace_path: If set, writes ``skill_prefetch.json`` for debugging.

    Returns:
        Formatted string of ``[Skill: name]\\ncontent`` blocks separated by
        ``---``, or ``""`` when the service is unavailable.
    """
    service_url = resolve_skills_service_url()
    if not service_url:
        logger.info(
            "prefetch_skills: no skills.service_url / SKILLS_SERVICE_URL — skipping"
        )
        _write_prefetch_debug(
            workspace_path,
            role,
            entries=[],
            meta={
                "status": "skipped",
                "reason": "no skills.service_url or SKILLS_SERVICE_URL",
            },
        )
        return ""

    manifest = _load_stack_manifest(workspace_path)
    queries = _queries_for_role(vision, role, extra_queries, manifest)

    seen_skills: set[str] = set()
    sections: list[str] = []
    debug_entries: list[dict] = []

    for q in queries:
        try:
            resp = httpx.post(
                f"{service_url}/query",
                json={"query": q, "top_k": top_k},
                timeout=15,
            )
            resp.raise_for_status()
            for r in resp.json().get("results", []):
                if not _passes_relevance(r):
                    logger.info(
                        "prefetch_skills [%s]: dropping low-relevance skill %r (score=%s < %.2f)",
                        role, r.get("skill_name"), r.get("score"), MIN_SKILL_SCORE,
                    )
                    continue
                skill_name = r["skill_name"]
                content = r.get("content") or ""
                if manifest and _skill_conflicts_with_manifest(skill_name, content, manifest):
                    logger.info(
                        "prefetch_skills [%s]: dropping skill %r — conflicts with stack_manifest",
                        role, skill_name,
                    )
                    continue
                if skill_name in seen_skills:
                    continue
                seen_skills.add(skill_name)
                sections.append(f"[Skill: {skill_name}]\n{content}")
                debug_entries.append({
                    "skill_name": skill_name,
                    "query": q,
                    "score": r.get("score"),
                    "content": content,
                })
        except Exception:
            logger.warning("prefetch_skills: query %r failed", q, exc_info=True)

    _write_prefetch_debug(
        workspace_path,
        role,
        entries=debug_entries,
        meta={
            "status": "ok" if debug_entries else "empty",
            "service_url": service_url,
            "queries": queries,
            "skill_count": len(debug_entries),
        },
    )

    if sections:
        logger.info(
            "prefetch_skills [%s]: injected %d skill sections (%s)",
            role, len(sections), ", ".join(sorted(seen_skills)),
        )
        if workspace_path and "job-" in workspace_path.name:
            job_id = workspace_path.name.split("job-")[-1]
            _record_skills_used(job_id, list(seen_skills))
    else:
        logger.info("prefetch_skills [%s]: no matching skills found", role)

    return "\n\n---\n\n".join(sections)
