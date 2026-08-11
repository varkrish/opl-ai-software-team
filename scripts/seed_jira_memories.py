#!/usr/bin/env python3
"""
One-time backfill: seed the context memory plane from closed Jira issues.

The webhook only writes memories for issues that arrive *after* the memory plane
goes live. This script backfills history so recall is useful on day one instead
of after 50 jobs.

Writes to the SHARED project (not a framework project) for the same reason the
webhook path does: a Jira issue describes what was built, not which framework
was used. See MemoryConfig.shared_project_id.

Usage:

    # Dry run first — always. Prints what would be written, writes nothing.
    python scripts/seed_jira_memories.py --projects ASSET,BILLING --dry-run

    # Then for real
    python scripts/seed_jira_memories.py --projects ASSET,BILLING \\
        --org-id acme-corp --limit 200

Requires:
    JIRA_BASE_URL   e.g. https://acme.atlassian.net
    JIRA_EMAIL + JIRA_API_TOKEN     (Jira Cloud), or
    JIRA_PERSONAL_ACCESS_TOKEN      (Jira Server/DC)
    MEMMACHINE_BASE_URL             e.g. http://localhost:8280

Idempotency: re-running writes duplicate episodes. The script records seeded
issue keys in a state file (--state-file, default .jira_seed_state.json) and
skips them on later runs. Delete that file only if you intend to re-seed.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

# Make the agent package importable when run from the repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
for candidate in (_REPO_ROOT / "agent" / "src", _REPO_ROOT):
    if candidate.exists() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

logger = logging.getLogger("seed_jira_memories")

_DEFAULT_JQL = (
    'project in ({projects}) AND statusCategory = Done '
    'ORDER BY updated DESC'
)
_PAGE_SIZE = 50


def _jira_auth() -> tuple[Optional[tuple], Dict[str, str]]:
    """Return (basic_auth_tuple, headers) for whichever Jira auth is configured."""
    pat = os.getenv("JIRA_PERSONAL_ACCESS_TOKEN")
    if pat:
        return None, {"Authorization": f"Bearer {pat}", "Accept": "application/json"}

    email = os.getenv("JIRA_EMAIL")
    token = os.getenv("JIRA_API_TOKEN")
    if email and token:
        return (email, token), {"Accept": "application/json"}

    raise SystemExit(
        "No Jira credentials found. Set JIRA_PERSONAL_ACCESS_TOKEN, or "
        "JIRA_EMAIL + JIRA_API_TOKEN."
    )


def fetch_issues(
    base_url: str,
    projects: List[str],
    *,
    limit: int,
    jql: Optional[str] = None,
) -> Iterable[Dict[str, Any]]:
    """Page through Jira search results, yielding raw issue dicts."""
    import requests

    auth, headers = _jira_auth()
    query = jql or _DEFAULT_JQL.format(
        projects=", ".join(f'"{p.strip()}"' for p in projects if p.strip())
    )
    logger.info("JQL: %s", query)

    fetched = 0
    start_at = 0
    while fetched < limit:
        page_size = min(_PAGE_SIZE, limit - fetched)
        response = requests.get(
            f"{base_url.rstrip('/')}/rest/api/2/search",
            params={
                "jql": query,
                "startAt": start_at,
                "maxResults": page_size,
                "fields": "summary,description,issuetype,status,project,resolution",
            },
            auth=auth,
            headers=headers,
            timeout=60,
        )
        if response.status_code != 200:
            raise SystemExit(
                f"Jira search failed ({response.status_code}): {response.text[:400]}"
            )
        payload = response.json()
        issues = payload.get("issues") or []
        if not issues:
            return

        for issue in issues:
            yield issue
            fetched += 1
            if fetched >= limit:
                return

        start_at += len(issues)
        if start_at >= int(payload.get("total") or 0):
            return


def _issue_to_summary(issue: Dict[str, Any]) -> tuple[str, Dict[str, str]]:
    """Build recall text and metadata for one closed Jira issue."""
    from llamaindex_crew.memory import build_jira_context_summary

    key = str(issue.get("key") or "")
    fields = issue.get("fields") or {}
    summary_text = str(fields.get("summary") or "").strip()
    issue_type = str((fields.get("issuetype") or {}).get("name") or "")
    status = str((fields.get("status") or {}).get("name") or "")
    project_key = str((fields.get("project") or {}).get("key") or "")

    description = fields.get("description")
    if isinstance(description, str) and description.strip():
        # Keep a little body text — the summary line alone is often too thin to
        # match a future vision semantically.
        first_para = " ".join(description.split())[:400]
        summary_text = f"{summary_text}. {first_para}"

    text = build_jira_context_summary(
        key, summary_text, issue_type=issue_type, status=status
    )
    return text, {
        "issue_key": key,
        "project_key": project_key,
        "issue_type": issue_type,
        "status": status,
        "seeded": "true",
    }


def _load_state(path: Path) -> set:
    if not path.exists():
        return set()
    try:
        return set(json.loads(path.read_text(encoding="utf-8")).get("seeded", []))
    except (json.JSONDecodeError, OSError):
        logger.warning("Could not read state file %s; treating as empty.", path)
        return set()


def _save_state(path: Path, seeded: set) -> None:
    try:
        path.write_text(
            json.dumps({"seeded": sorted(seeded)}, indent=2) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        logger.warning("Could not write state file %s: %s", path, exc)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Seed the context memory plane from closed Jira issues."
    )
    parser.add_argument(
        "--projects", required=True,
        help="Comma-separated Jira project keys, e.g. ASSET,BILLING",
    )
    parser.add_argument(
        "--org-id", default=None,
        help="Customer scope (org_id). Defaults to memory.default_org_id from config.",
    )
    parser.add_argument("--limit", type=int, default=200, help="Max issues to seed.")
    parser.add_argument("--jql", default=None, help="Override the default JQL.")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be written without writing. Do this first.",
    )
    parser.add_argument(
        "--state-file", default=".jira_seed_state.json",
        help="Tracks already-seeded issue keys so re-runs do not duplicate.",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    base_url = os.getenv("JIRA_BASE_URL")
    if not base_url:
        raise SystemExit("JIRA_BASE_URL is not set.")

    projects = [p.strip() for p in args.projects.split(",") if p.strip()]
    if not projects:
        raise SystemExit("--projects produced no project keys.")

    from llamaindex_crew.config import ConfigLoader
    from llamaindex_crew.memory import MemoryScope
    from llamaindex_crew.memory.context_memory import ContextMemory

    try:
        config = ConfigLoader.load()
    except Exception as exc:
        raise SystemExit(f"Could not load config: {exc}")

    memory_config = getattr(config, "memory", None)
    if not getattr(memory_config, "enabled", False) and not args.dry_run:
        raise SystemExit(
            "memory.enabled is false. Set MEMORY_ENABLED=true (or enable it in "
            "config.yaml) before seeding."
        )

    org_id = args.org_id or getattr(memory_config, "default_org_id", "default")
    shared_project = getattr(memory_config, "shared_project_id", "shared-context")
    base = (
        getattr(memory_config, "base_url", None)
        or os.getenv("MEMMACHINE_BASE_URL")
        or os.getenv("MEMORY_BACKEND_URL")
    )
    if not base and not args.dry_run:
        raise SystemExit("No MemMachine base_url. Set MEMMACHINE_BASE_URL.")

    state_path = Path(args.state_file)
    seeded = _load_state(state_path)
    logger.info(
        "Seeding org=%s project=%s (already seeded: %d)", org_id, shared_project, len(seeded)
    )

    written = 0
    skipped = 0
    failed = 0
    # One Memory handle per Jira project so group_id (domain) scoping matches what
    # the webhook path writes for live issues.
    handles: Dict[str, ContextMemory] = {}

    try:
        for issue in fetch_issues(
            base_url, projects, limit=args.limit, jql=args.jql
        ):
            key = str(issue.get("key") or "")
            if not key:
                continue
            if key in seeded:
                skipped += 1
                continue

            text, metadata = _issue_to_summary(issue)
            domain = (metadata.get("project_key") or "general").lower()

            if args.dry_run:
                print(f"[dry-run] {domain}: {text[:200]}")
                written += 1
                continue

            handle = handles.get(domain)
            if handle is None:
                handle = ContextMemory(
                    MemoryScope(
                        org_id=str(org_id),
                        project_id=str(shared_project),
                        domain=domain,
                        agent_id="jira_seed",
                    ),
                    base_url=base,
                    api_key=getattr(memory_config, "api_key", None)
                    or os.getenv("MEMMACHINE_API_KEY"),
                    timeout_seconds=int(getattr(memory_config, "timeout_seconds", 15) or 15),
                    enabled=True,
                )
                handles[domain] = handle

            if handle.add(
                text, memory_type="jira_context", producer="jira_seed", metadata=metadata
            ):
                written += 1
                seeded.add(key)
            else:
                failed += 1
                logger.warning("Write failed for %s", key)
    finally:
        for handle in handles.values():
            handle.close()
        if not args.dry_run:
            _save_state(state_path, seeded)

    logger.info(
        "Done — written=%d skipped(already seeded)=%d failed=%d", written, skipped, failed
    )
    if args.dry_run:
        logger.info("Dry run: nothing was written and no state was saved.")
    return 1 if failed and not written else 0


if __name__ == "__main__":
    sys.exit(main())
