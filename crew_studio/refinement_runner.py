"""
Refinement runner: git snapshot, context loading, RefinementAgent execution.
Runs in a thread; does not set WORKSPACE_PATH env (thread-safe).
"""
import logging
import os
from pathlib import Path
from typing import Callable, Optional, List, Dict, Any

logger = logging.getLogger(__name__)

# Maximum retries when the agent completes without writing files
MAX_AGENT_RETRIES = 2


def _git_snapshot(workspace_path: Path) -> str:
    """Create a pre-refinement git commit in the workspace. Returns message."""
    try:
        import git
    except ImportError:
        logger.warning("gitpython not installed; skipping pre-refinement snapshot")
        return "Git snapshot skipped (git not available)"
    if os.getenv("ENABLE_GIT", "true").lower() not in ("true", "1", "yes"):
        return "Git snapshot skipped (ENABLE_GIT=false)"
    workspace_path = Path(workspace_path)
    try:
        if not (workspace_path / ".git").exists():
            repo = git.Repo.init(workspace_path)
            gitignore = workspace_path / ".gitignore"
            if not gitignore.exists():
                gitignore.write_text("__pycache__/\n*.pyc\n.pytest_cache/\n.coverage\nhtmlcov/\n.env\n", encoding="utf-8")
            repo.index.add([".gitignore"])
        else:
            repo = git.Repo(workspace_path)
        repo.git.add(A=True)
        if repo.is_dirty(untracked_files=True) or repo.untracked_files:
            commit = repo.index.commit("pre-refinement snapshot")
            return f"Git snapshot created: {commit.hexsha[:7]}"
        return "No changes to snapshot (working tree clean)"
    except Exception as e:
        logger.warning("Pre-refinement git snapshot failed: %s", e)
        return f"Git snapshot skipped: {e}"


def _pull_and_reindex(workspace_path: Path, job_db: Any, job_id: str) -> None:
    """Pull latest changes from the git remote (if any) and re-warm the tldr cache.

    Ensures refinements operate on top of the freshest upstream code, and that
    the tldr call-graph index reflects what's actually on disk. Best-effort:
    any failure here is logged and refinement proceeds with whatever is present.
    """
    workspace_path = Path(workspace_path)
    if not (workspace_path / ".git").exists():
        return
    try:
        import git
    except ImportError:
        return

    try:
        repo = git.Repo(workspace_path)
    except Exception as e:
        logger.warning("Could not open git repo for pull: %s", e)
        return

    if not repo.remotes:
        return

    try:
        origin = repo.remote("origin")
    except ValueError:
        return

    from crew_studio.github_client import resolve_github_token
    token = resolve_github_token(job_db, job_id)

    original_url = origin.url
    auth_url = original_url
    if token:
        from llamaindex_crew.utils.git_remote_auth import inject_push_credentials
        auth_url = inject_push_credentials(original_url, token)
        if auth_url != original_url:
            origin.set_url(auth_url)

    try:
        origin.pull(rebase=True)
        logger.info("Pulled latest changes from origin before refinement (job %s)", job_id)
    except Exception as e:
        logger.warning("git pull before refinement failed (non-fatal): %s", e)
    finally:
        if auth_url != original_url:
            origin.set_url(original_url)

    try:
        from llamaindex_crew.tools.tldr_tools import run_tldr
        run_tldr(["warm", str(workspace_path), "--lang", "all"])
        logger.info("tldr warm completed after pull (job %s)", job_id)
    except Exception as e:
        logger.warning("tldr warm after pull failed (non-fatal): %s", e)


def _workspace_has_changes(workspace_path: Path) -> bool:
    """Check if the workspace has uncommitted changes (i.e. agent wrote files)."""
    try:
        import git
    except ImportError:
        # Can't verify without git; assume changes were made
        return True
    workspace_path = Path(workspace_path)
    try:
        if not (workspace_path / ".git").exists():
            return True  # No git repo; can't verify
        repo = git.Repo(workspace_path)
        # Check for any modified, added, or untracked files
        return repo.is_dirty(untracked_files=True) or bool(repo.untracked_files)
    except Exception as e:
        logger.warning("Could not check workspace changes: %s", e)
        return True  # Err on the side of trusting the agent


def _load_tech_stack(workspace_path: Path) -> Optional[str]:
    """Load tech_stack.md content if present."""
    p = workspace_path / "tech_stack.md"
    if p.exists():
        try:
            return p.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("Could not read tech_stack.md: %s", e)
    return None


def _load_file_listing(workspace_path: Path) -> str:
    """Recursively list workspace files (for agent context). Uses file_tools with explicit path."""
    from src.llamaindex_crew.tools.file_tools import file_lister
    return file_lister(".", workspace_path=str(workspace_path))


def _load_file_content(workspace_path: Path, file_path: str) -> Optional[str]:
    """Load content of a file relative to workspace."""
    from src.llamaindex_crew.tools.file_tools import file_reader
    try:
        return file_reader(file_path, workspace_path=str(workspace_path))
    except Exception as e:
        logger.warning("Could not read %s: %s", file_path, e)
        return None


# File extensions considered as modifiable source code
_SOURCE_EXTENSIONS = {
    '.js', '.jsx', '.ts', '.tsx', '.py', '.java', '.rb', '.go', '.rs',
    '.c', '.cpp', '.h', '.hpp', '.cs', '.swift', '.kt',
    '.html', '.htm', '.css', '.scss', '.less', '.sass',
    '.json', '.yaml', '.yml', '.toml', '.xml',
    '.md', '.txt', '.sh', '.bash',
}

# Directories to skip during recursive scanning
_SKIP_DIRS = {'.git', '__pycache__', 'node_modules', '.pytest_cache', 'htmlcov', '.tox', 'venv', '.venv'}

# Internal job artifacts generated by the platform — never hand these to the refinement agent
_INTERNAL_ARTIFACT_PATTERNS = (
    "state_", "tasks_", "tech_stack.md", "import_index_manifest.json",
    "delivery_mode_triage.json", "agent_backstories.json", "crew_errors.log",
)

# Max characters to inline per file (prevent huge files from blowing the context)
_MAX_INLINE_CHARS = 8000

# Max total characters for all inlined files combined
_MAX_TOTAL_INLINE_CHARS = 50000


def _is_internal_artifact(name: str) -> bool:
    """Return True if the filename is a platform-generated artifact that agents must not touch."""
    return any(name.startswith(p) or name == p for p in _INTERNAL_ARTIFACT_PATTERNS)


def _discover_source_files(workspace_path: Path) -> List[str]:
    """Recursively discover modifiable source files in the workspace.
    Excludes internal job artifacts and returns relative paths sorted alphabetically."""
    workspace_path = Path(workspace_path)
    results: List[str] = []
    for item in sorted(workspace_path.rglob("*")):
        if any(part in _SKIP_DIRS for part in item.parts):
            continue
        if not item.is_file():
            continue
        if _is_internal_artifact(item.name):
            continue
        if item.suffix.lower() in _SOURCE_EXTENSIONS:
            results.append(str(item.relative_to(workspace_path)))
    return results


def _preload_source_files(workspace_path: Path, file_paths: List[str]) -> Dict[str, str]:
    """Pre-read source files, respecting per-file and total size limits.
    Returns dict mapping relative path -> content (truncated if needed)."""
    contents: Dict[str, str] = {}
    total = 0
    for fp in file_paths:
        if total >= _MAX_TOTAL_INLINE_CHARS:
            break
        full = workspace_path / fp
        try:
            text = full.read_text(encoding="utf-8", errors="replace")
            if len(text) > _MAX_INLINE_CHARS:
                text = text[:_MAX_INLINE_CHARS] + "\n... (truncated)"
            contents[fp] = text
            total += len(text)
        except Exception as e:
            logger.warning("Could not preload %s: %s", fp, e)
    return contents


def run_refinement(
    job_id: str,
    workspace_path: Path,
    prompt: str,
    refinement_id: str,
    job_db: Any,
    progress_callback: Callable[[str, int, Optional[str]], None],
    file_path: Optional[str] = None,
    previous_status: str = "completed",
    enhanced: bool = False,
    scope: Optional[str] = None,
    refinement_kind: Optional[str] = None,
) -> Dict[str, Any]:
    from src.llamaindex_crew.config import ConfigLoader
    from src.llamaindex_crew.utils.llm_config import user_llm_context
    fallback_config = ConfigLoader.load()
    with user_llm_context(job_id, job_db, fallback_config):
        return _run_refinement_impl(
            job_id=job_id,
            workspace_path=workspace_path,
            prompt=prompt,
            refinement_id=refinement_id,
            job_db=job_db,
            progress_callback=progress_callback,
            file_path=file_path,
            previous_status=previous_status,
            enhanced=enhanced,
            scope=scope,
            refinement_kind=refinement_kind,
        )


def _run_refinement_impl(
    job_id: str,
    workspace_path: Path,
    prompt: str,
    refinement_id: str,
    job_db: Any,
    progress_callback: Callable[[str, int, Optional[str]], None],
    file_path: Optional[str] = None,
    previous_status: str = "completed",
    enhanced: bool = False,
    scope: Optional[str] = None,
    refinement_kind: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run refinement: snapshot, build context, run RefinementAgent, update DB.

    Call this from a background thread. Does not mutate os.environ.

    Args:
        job_id: Job ID (for budget and DB).
        workspace_path: Job workspace root.
        prompt: User refinement prompt.
        refinement_id: ID for the refinement record.
        job_db: JobDatabase instance.
        progress_callback: callback(phase, progress, message).
        file_path: Optional target file path (relative to workspace).
        previous_status: Job status to restore when refinement completes or fails.
        enhanced: True for import-mode jobs (`job_mode=import`); uses richer project-wide
            refinement when EnhancedRefinementAgent is available; otherwise same as project-wide.
        scope: ``impact`` | ``file`` | ``project``. Default: impact when file_path set, else project.
        refinement_kind: ``fix`` | ``feature`` | ``edit`` — affects post-fix gates and commit message.

    Returns:
        {"status": "success"} or {"status": "error", "error": "..."}.
    """
    workspace_path = Path(workspace_path)
    progress_callback("refining", 2, "Syncing with remote repository...")
    _pull_and_reindex(workspace_path, job_db, job_id)

    progress_callback("refining", 5, "Creating git snapshot...")
    _git_snapshot(workspace_path)

    if enhanced:
        logger.info("Import job refinement (enhanced=True): using project-wide refinement path")

    progress_callback("refining", 10, "Loading context...")
    tech_stack_content = _load_tech_stack(workspace_path)
    file_listing = _load_file_listing(workspace_path)
    refinement_history = job_db.get_refinement_history(job_id, limit=10)
    # Exclude current (running) refinement from history context
    refinement_history = [r for r in refinement_history if r.get("id") != refinement_id]

    project_context = _load_project_context(workspace_path, job_db, job_id, prompt)

    meta = _job_metadata(job_db, job_id)
    if refinement_kind is None:
        refinement_kind = meta.get("refinement_kind")
    if scope is None:
        scope = "impact" if file_path else "project"

    if file_path and scope == "impact":
        return _run_impact_refinement(
            job_id=job_id,
            workspace_path=workspace_path,
            prompt=prompt,
            refinement_id=refinement_id,
            job_db=job_db,
            progress_callback=progress_callback,
            file_path=file_path,
            tech_stack_content=tech_stack_content,
            file_listing=file_listing,
            refinement_history=refinement_history,
            previous_status=previous_status,
            project_context=project_context,
            refinement_kind=refinement_kind,
        )

    if file_path and scope == "file":
        # ── FILE-LEVEL SCOPE ──────────────────────────────────────────
        return _run_single_file_refinement(
            job_id=job_id,
            workspace_path=workspace_path,
            prompt=prompt,
            refinement_id=refinement_id,
            job_db=job_db,
            progress_callback=progress_callback,
            file_path=file_path,
            tech_stack_content=tech_stack_content,
            file_listing=file_listing,
            refinement_history=refinement_history,
            previous_status=previous_status,
            scope="file",
            project_context=project_context,
            refinement_kind=refinement_kind,
        )

    # ── PROJECT-WIDE SCOPE ──────────────────────────────────────────────
    # scope == "project" — reached even when a file is open in the UI (e.g.
    # user has a file selected but explicitly chose "Whole project"). A
    # previous version of this dispatcher fell through to single-file
    # refinement any time file_path was set, which silently downgraded
    # "project" scope to "file" scope. file_path is passed through only as a
    # hint so the selected file is still prioritized among the candidates.
    return _run_project_wide_refinement(
        job_id=job_id,
        workspace_path=workspace_path,
        prompt=prompt,
        refinement_id=refinement_id,
        job_db=job_db,
        progress_callback=progress_callback,
        tech_stack_content=tech_stack_content,
        file_listing=file_listing,
        refinement_history=refinement_history,
        previous_status=previous_status,
        project_context=project_context,
        refinement_kind=refinement_kind,
        hint_file_path=file_path,
    )


def _job_metadata(job_db: Any, job_id: str) -> dict:
    try:
        from crew_studio.work_intent import parse_metadata
        job = job_db.get_job(job_id)
        return parse_metadata(job.get("metadata") if job else None)
    except Exception:
        return {}


def _load_project_context(workspace_path: Path, job_db: Any, job_id: str, prompt: str) -> str:
    try:
        from src.llamaindex_crew.utils.refinement_context import load_refinement_context
        ctx = load_refinement_context(workspace_path, job_db, job_id, user_prompt=prompt)
        return ctx.format_for_prompt()
    except Exception as exc:
        logger.warning("Could not load refinement project context: %s", exc)
        return ""


def trigger_auto_fix_after_analyze(
    job_id: str,
    workspace_path: Path,
    job_db: Any,
    progress_callback: Callable[[str, int, Optional[str]], None],
) -> Dict[str, Any]:
    """Run fix refinement automatically after import analysis (JIRA bug / mode=fix)."""
    import uuid as _uuid
    meta = _job_metadata(job_db, job_id)
    if not meta.get("auto_fix_after_analyze"):
        return {"status": "skipped", "reason": "auto_fix_after_analyze not set"}

    job = job_db.get_job(job_id) or {}
    prompt = (job.get("vision") or "").strip()
    if not prompt:
        return {"status": "error", "error": "No vision/prompt for auto fix"}

    refinement_id = str(_uuid.uuid4())
    job_db.create_refinement(refinement_id, job_id, prompt, None)
    job_db.update_job(job_id, {"status": "running"})
    job_db.update_progress(job_id, "refining", 0, "Auto fix refinement started.")

    return run_refinement(
        job_id=job_id,
        workspace_path=Path(workspace_path),
        prompt=prompt,
        refinement_id=refinement_id,
        job_db=job_db,
        progress_callback=progress_callback,
        file_path=None,
        previous_status="completed",
        enhanced=True,
        scope="project",
        refinement_kind=meta.get("refinement_kind", "fix"),
    )


def _run_impact_refinement(
    job_id: str,
    workspace_path: Path,
    prompt: str,
    refinement_id: str,
    job_db: Any,
    progress_callback: Callable[[str, int, Optional[str]], None],
    file_path: str,
    tech_stack_content: Optional[str],
    file_listing: Optional[str],
    refinement_history: List[Dict[str, Any]],
    previous_status: str = "completed",
    project_context: str = "",
    refinement_kind: Optional[str] = None,
) -> Dict[str, Any]:
    """Impact-aware refinement: primary file + related files in one batched pass."""
    from src.llamaindex_crew.utils.refinement_impact import discover_impact_files

    all_sources = _discover_source_files(workspace_path)
    allowed = discover_impact_files(
        workspace_path, prompt, file_path, max_files=5, all_source_files=all_sources,
    )
    progress_callback("refining", 20, f"Impact scope: {', '.join(allowed)}")

    preloaded = _preload_source_files(workspace_path, allowed)
    if not preloaded:
        return _fail_refinement(
            job_db, job_id, refinement_id, progress_callback,
            f"Could not load files for impact scope: {allowed}", previous_status,
        )

    try:
        from src.llamaindex_crew.agents.refinement_agent import RefinementAgent
        agent = RefinementAgent(workspace_path=workspace_path, project_id=job_id)
        agent.run(
            user_prompt=prompt,
            candidate_files=preloaded,
            tech_stack_content=tech_stack_content,
            refinement_history=refinement_history,
            project_context=project_context,
        )
    except Exception as e:
        logger.exception("Impact refinement failed: %s", e)
        return _fail_refinement(job_db, job_id, refinement_id, progress_callback, str(e), previous_status)

    if not _workspace_has_changes(workspace_path):
        return _fail_refinement(
            job_db, job_id, refinement_id, progress_callback,
            "Impact refinement completed but no files were changed.", previous_status,
        )

    _post_fix_gates(workspace_path, job_db, job_id, refinement_kind)
    summary = f"Refinement completed — updated {len(allowed)} file(s) in impact scope: {', '.join(allowed)}"
    return _complete_refinement(
        workspace_path, job_db, job_id, refinement_id, progress_callback, summary, previous_status,
    )


def _run_single_file_refinement(
    job_id: str,
    workspace_path: Path,
    prompt: str,
    refinement_id: str,
    job_db: Any,
    progress_callback: Callable[[str, int, Optional[str]], None],
    file_path: str,
    tech_stack_content: Optional[str],
    file_listing: Optional[str],
    refinement_history: List[Dict[str, Any]],
    previous_status: str = "completed",
    scope: str = "file",
    project_context: str = "",
    refinement_kind: Optional[str] = None,
) -> Dict[str, Any]:
    """Run refinement targeting a single file. Retries once if no files written."""
    initial_file_content = _load_file_content(workspace_path, file_path)

    progress_callback("refining", 20, f"Refining {file_path}...")
    agent_response = None
    files_changed = False

    for attempt in range(1, MAX_AGENT_RETRIES + 1):
        try:
            from src.llamaindex_crew.agents.refinement_agent import RefinementAgent
            agent = RefinementAgent(workspace_path=workspace_path, project_id=job_id)

            if attempt > 1:
                progress_callback("refining", 25,
                    f"Retry {attempt}/{MAX_AGENT_RETRIES}: agent didn't write files, re-running...")
                logger.warning("File refinement attempt %d/%d for %s", attempt, MAX_AGENT_RETRIES, file_path)

            agent_response = agent.run(
                user_prompt=prompt,
                file_path=file_path,
                tech_stack_content=tech_stack_content,
                file_listing=file_listing,
                refinement_history=refinement_history,
                initial_file_content=initial_file_content,
                scope=scope,
                allowed_files=[file_path],
                project_context=project_context,
            )
            logger.info("File refinement response (attempt %d, %s): %s",
                        attempt, file_path, str(agent_response)[:500])
        except Exception as e:
            logger.exception("File refinement failed for %s: %s", file_path, e)
            return _fail_refinement(job_db, job_id, refinement_id, progress_callback, str(e), previous_status)

        # Verify: either git shows changes (write/modify) or the target file was deleted
        progress_callback("refining", 80, "Verifying changes...")
        file_existed_before = initial_file_content is not None
        file_deleted_now = file_existed_before and not (workspace_path / file_path).exists()
        files_changed = _workspace_has_changes(workspace_path) or file_deleted_now
        if files_changed:
            if file_deleted_now:
                logger.info("File refinement deleted %s (attempt %d)", file_path, attempt)
            else:
                logger.info("File refinement wrote changes for %s (attempt %d)", file_path, attempt)
            break
        else:
            logger.warning("File refinement wrote no files for %s (attempt %d/%d)",
                           file_path, attempt, MAX_AGENT_RETRIES)

    if not files_changed:
        error_msg = (
            f"The AI agent completed but did not modify {file_path}. "
            "Try rephrasing your request or being more specific."
        )
        return _fail_refinement(job_db, job_id, refinement_id, progress_callback, error_msg, previous_status)

    summary = f"Refinement completed — {file_path} deleted." if (file_existed_before and not (workspace_path / file_path).exists()) else f"Refinement completed — {file_path} updated."
    _post_fix_gates(workspace_path, job_db, job_id, refinement_kind)
    return _complete_refinement(workspace_path, job_db, job_id, refinement_id, progress_callback, summary, previous_status)


def _candidate_files_from_prompt(prompt: str, source_files: List[str], workspace_path: Path) -> List[str]:
    """Narrow source_files to only those that likely need changes for this prompt.

    Strategy:
    1. Strip URLs from the prompt so domain/path words don't become false-positive tokens.
    2. Extract identifier-like tokens (especially patterns with underscores like ff_*).
    3. Return only files whose content contains those tokens.
    4. Fall back to full list when no specific tokens are found or nothing matches.
    """
    import re as _re

    # Strip URLs first — we don't want "frappe", "github", "mcp" from a URL polluting tokens
    prompt_no_urls = _re.sub(r'https?://\S+', '', prompt)

    # 1. Glob/wildcard patterns like ff_*, SomePrefix_* — most specific signal
    raw_tokens = _re.findall(r'([\w]+(?:_[\w]*)*)\*', prompt_no_urls)
    # 2. Backtick or quote wrapped identifiers: `ff_graph`, "MyFunc"
    raw_tokens += _re.findall(r'[`"\']([^`"\']{2,40})[`"\']', prompt_no_urls)
    # 3. snake_case identifiers (at least one underscore between words)
    raw_tokens += _re.findall(r'\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b', prompt_no_urls)
    if not raw_tokens:
        # Fallback: plain words ≥5 chars that look like identifiers
        raw_tokens = _re.findall(r'\b([A-Za-z][A-Za-z0-9]{4,})\b', prompt_no_urls)

    _STOPWORDS = {
        "the", "this", "that", "and", "for", "have", "only", "with", "from",
        "into", "change", "remove", "update", "tool", "tools", "all", "other",
        "use", "code", "file", "files", "project", "just", "should", "also",
    }
    tokens = list({
        t.rstrip("*") for t in raw_tokens
        if len(t) >= 3 and t.lower().rstrip("*") not in _STOPWORDS
    })

    if not tokens:
        return source_files

    logger.info("Candidate filter tokens: %s", tokens)

    candidates: List[str] = []
    for fp in source_files:
        full = workspace_path / fp
        try:
            content = full.read_text(encoding="utf-8", errors="replace")
        except Exception:
            candidates.append(fp)
            continue
        if any(tok in content for tok in tokens):
            candidates.append(fp)

    logger.info("Candidate files (%d/%d): %s", len(candidates), len(source_files), candidates)
    return candidates if candidates else source_files


def _run_project_wide_refinement(
    job_id: str,
    workspace_path: Path,
    prompt: str,
    refinement_id: str,
    job_db: Any,
    progress_callback: Callable[[str, int, Optional[str]], None],
    tech_stack_content: Optional[str],
    file_listing: Optional[str],
    refinement_history: List[Dict[str, Any]],
    previous_status: str = "completed",
    project_context: str = "",
    refinement_kind: Optional[str] = None,
    hint_file_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Run project-wide refinement: filter candidates, batch edit when ≤5 files.

    hint_file_path: the file the user had open in the UI when they chose
    "Whole project" scope. Not a hard restriction — the refinement still
    considers the whole project — but it's guaranteed to be included among
    the candidates since the user was almost certainly looking at it for a
    reason.
    """
    progress_callback("refining", 12, "Scanning project files...")
    all_source_files = _discover_source_files(workspace_path)
    if not all_source_files:
        return _fail_refinement(job_db, job_id, refinement_id, progress_callback,
                                "No source files found in the workspace.", previous_status)

    source_files = _candidate_files_from_prompt(prompt, all_source_files, workspace_path)
    if hint_file_path and hint_file_path in all_source_files and hint_file_path not in source_files:
        source_files = [hint_file_path] + source_files
    total = len(source_files)
    logger.info(
        "Project-wide scope: %d/%d candidate files: %s",
        total, len(all_source_files), source_files,
    )
    progress_callback("refining", 15, f"Refining {total} candidate file(s)...")

    _git_snapshot(workspace_path)

    if total <= 5:
        preloaded = _preload_source_files(workspace_path, source_files)
        if not preloaded:
            return _fail_refinement(
                job_db, job_id, refinement_id, progress_callback,
                "Could not preload candidate files.", previous_status,
            )
        try:
            from src.llamaindex_crew.agents.refinement_agent import RefinementAgent
            agent = RefinementAgent(workspace_path=workspace_path, project_id=job_id)
            agent.run(
                user_prompt=prompt,
                candidate_files=preloaded,
                tech_stack_content=tech_stack_content,
                refinement_history=refinement_history,
                project_context=project_context,
            )
        except Exception as e:
            logger.exception("Batched project refinement failed: %s", e)
            return _fail_refinement(job_db, job_id, refinement_id, progress_callback, str(e), previous_status)

        if not _workspace_has_changes(workspace_path):
            return _fail_refinement(
                job_db, job_id, refinement_id, progress_callback,
                f"Processed {total} candidate files but none were changed.",
                previous_status,
            )
        _post_fix_gates(workspace_path, job_db, job_id, refinement_kind)
        summary = f"Refinement completed — batched edit of {len(preloaded)} file(s)"
        return _complete_refinement(
            workspace_path, job_db, job_id, refinement_id, progress_callback, summary, previous_status,
        )

    # >5 candidates: process in batches of 5
    files_modified: List[str] = []
    for batch_start in range(0, total, 5):
        batch = source_files[batch_start: batch_start + 5]
        preloaded = _preload_source_files(workspace_path, batch)
        if not preloaded:
            continue
        try:
            from src.llamaindex_crew.agents.refinement_agent import RefinementAgent
            agent = RefinementAgent(workspace_path=workspace_path, project_id=job_id)
            agent.run(
                user_prompt=prompt,
                candidate_files=preloaded,
                tech_stack_content=tech_stack_content,
                refinement_history=refinement_history,
                project_context=project_context,
            )
            for fp in batch:
                if _workspace_has_changes(workspace_path):
                    files_modified.append(fp)
        except Exception as e:
            logger.warning("Batch refinement error: %s", e)

    if not files_modified and not _workspace_has_changes(workspace_path):
        return _fail_refinement(
            job_db, job_id, refinement_id, progress_callback,
            f"Processed {total} candidate files but none were changed.",
            previous_status,
        )
    _post_fix_gates(workspace_path, job_db, job_id, refinement_kind)
    summary = f"Refinement completed — modified files in {total} candidate set"
    return _complete_refinement(
        workspace_path, job_db, job_id, refinement_id, progress_callback, summary, previous_status,
    )


# ── Helpers ────────────────────────────────────────────────────────────────────

def _post_fix_gates(workspace_path: Path, job_db: Any, job_id: str, refinement_kind: Optional[str]) -> None:
    """After fix refinements: optional validator + git commit with fix() message."""
    if refinement_kind != "fix":
        return
    meta = _job_metadata(job_db, job_id)
    issue_key = meta.get("jira_issue_key") or meta.get("jira_epic_key") or "BUG"
    summary = ""
    try:
        job = job_db.get_job(job_id)
        vision = (job.get("vision") or "") if job else ""
        summary = vision.split("\n", 1)[0].replace(f"Fix JIRA {issue_key}:", "").strip()[:72]
    except Exception:
        pass

    validator_url = os.getenv("VALIDATOR_URL", "").strip()
    if validator_url:
        try:
            import httpx
            r = httpx.post(
                f"{validator_url.rstrip('/')}/api/v1/validate",
                json={"workspace_path": str(workspace_path), "checks": ["syntax", "imports"]},
                timeout=120.0,
            )
            if r.status_code >= 400:
                logger.warning("Post-fix validator returned %s: %s", r.status_code, r.text[:200])
        except Exception as exc:
            logger.warning("Post-fix validator call failed: %s", exc)

    try:
        from src.llamaindex_crew.utils.fix_prompt import fix_commit_message
        import git as gitmodule
        msg = fix_commit_message(str(issue_key), summary or "bug fix")
        if (workspace_path / ".git").exists():
            repo = gitmodule.Repo(workspace_path)
            repo.git.add(A=True)
            if repo.is_dirty(untracked_files=True) or repo.untracked_files:
                repo.index.commit(msg)
    except Exception as exc:
        logger.warning("Post-fix git commit failed: %s", exc)


def _user_friendly_llm_error(raw_error: str) -> str:
    """Turn raw LLM/proxy errors (503, 502, 429, etc.) into a short message for the refine UI."""
    s = raw_error.strip()
    if "503" in s or "Service Unavailable" in s.lower():
        return (
            "LLM service temporarily unavailable (503). "
            "Try again in a few minutes or check your LLM endpoint configuration."
        )
    if "502" in s or "Bad Gateway" in s.lower():
        return (
            "LLM gateway error (502). The AI service may be overloaded. "
            "Try again in a few minutes."
        )
    if "429" in s or "rate limit" in s.lower() or "quota" in s.lower():
        return (
            "LLM rate limit or quota exceeded (429). "
            "Wait a moment and try again, or check your API quota."
        )
    if "timeout" in s.lower() or "timed out" in s.lower():
        return "The AI request timed out. Try again or use a shorter refinement prompt."
    # Keep original if it's short enough; otherwise truncate and add hint
    if len(s) > 200:
        return s[:200] + "… Try rephrasing your request or selecting a different file."
    return s


def _fail_refinement(job_db, job_id, refinement_id, progress_callback, error_msg, previous_status: str = "completed"):
    """Mark refinement as failed and restore job status so dashboard shows correct state."""
    logger.error("Refinement failed: %s", error_msg)
    friendly_msg = _user_friendly_llm_error(error_msg)
    job_db.fail_refinement(refinement_id, friendly_msg)
    job_db.update_job(job_id, {
        "status": previous_status,
        "current_phase": "completed",
        "progress": 100,
        "error": None,
    })
    progress_callback("refinement_failed", 0, friendly_msg)
    return {"status": "error", "error": friendly_msg}


def _create_github_pr(workspace_path: Path, job_id: str, prompt: str, job_db) -> Optional[str]:
    """Push a new branch and open a GitHub PR for the refinement changes.

    Returns the PR URL on success, or None if skipped / failed.
    Requires a GitHub token (user's Settings PAT, or env GITHUB_TOKEN as fallback)
    and a git remote named 'origin' pointing to GitHub.
    """
    import re as _re
    from crew_studio.github_client import resolve_github_token
    token = resolve_github_token(job_db, job_id)
    if not token:
        logger.warning("No GitHub token available (Settings or GITHUB_TOKEN) — skipping PR creation")
        return None

    try:
        import git as _git
        import urllib.request as _req
        import json as _json_pr

        repo = _git.Repo(workspace_path)
        if not repo.remotes:
            logger.warning("No git remote — skipping PR creation")
            return None

        origin_url = repo.remotes["origin"].url
        # Only handle github.com repos
        m = _re.search(r"github\.com[/:](.+?/.+?)(?:\.git)?$", origin_url)
        if not m:
            logger.warning("Remote is not GitHub (%s) — skipping PR creation", origin_url)
            return None
        repo_slug = m.group(1)  # e.g. "vyogotech/frappe-mcp-server"

        # Create and checkout a new branch
        branch = f"crew-ai/job-{job_id[:8]}"
        try:
            repo.git.checkout("-b", branch)
        except _git.exc.GitCommandError:
            # Branch may already exist — try switching to it
            repo.git.checkout(branch)

        # Stage and commit everything not yet committed
        git_name = os.getenv("GITHUB_USER_NAME", "Crew AI").strip() or "Crew AI"
        git_email = os.getenv("GITHUB_USER_EMAIL", "crew-ai@noreply.github.com").strip() or "crew-ai@noreply.github.com"
        actor = _git.Actor(git_name, git_email)
        repo.git.add(A=True)
        if repo.is_dirty(untracked_files=True) or repo.untracked_files:
            repo.index.commit(
                f"crew-ai: {prompt[:72]}",
                author=actor,
                committer=actor,
            )

        # Inject token into remote URL for push auth
        from llamaindex_crew.utils.git_remote_auth import push_with_token

        push_result = push_with_token(repo, remote_name="origin", branch_name=branch, token=token)
        if not push_result.success:
            logger.warning("Refinement push failed: %s", push_result.message)
            return None

        # Detect the default branch via GitHub API
        default_branch = "main"
        try:
            repo_api_url = f"https://api.github.com/repos/{repo_slug}"
            repo_req = _req.Request(repo_api_url, headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            })
            with _req.urlopen(repo_req, timeout=10) as repo_resp:
                repo_info = _json_pr.loads(repo_resp.read())
            default_branch = repo_info.get("default_branch", "main")
        except Exception:
            pass
        logger.info("PR base branch: %s (repo: %s)", default_branch, repo_slug)

        # Create the PR via GitHub API
        pr_body = {
            "title": f"[Crew AI] {prompt[:72]}",
            "head": branch,
            "base": default_branch,
            "body": (
                f"**Automated PR from Crew AI**\n\n"
                f"**Job:** `{job_id}`\n\n"
                f"**Request:**\n> {prompt}\n\n"
                "---\n*Generated by OPL Crew AI import & iterate workflow.*"
            ),
        }
        api_url = f"https://api.github.com/repos/{repo_slug}/pulls"
        req = _req.Request(
            api_url,
            data=_json_pr.dumps(pr_body).encode(),
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with _req.urlopen(req, timeout=15) as resp:
            pr_data = _json_pr.loads(resp.read())
        pr_url = pr_data.get("html_url", "")
        logger.info("PR created: %s", pr_url)
        return pr_url

    except Exception as e:
        logger.warning("PR creation failed (non-fatal): %s", e)
        return None


def _complete_refinement(workspace_path, job_db, job_id, refinement_id, progress_callback, message, previous_status: str = "completed"):
    """Mark refinement as completed and restore job status so dashboard shows correct state."""
    try:
        _git_snapshot(workspace_path)
    except Exception as e:
        logger.warning("Post-refinement git snapshot failed: %s", e)

    # Attempt to push a branch and open a PR if this is an import job with a GitHub remote
    pr_url: Optional[str] = None
    try:
        job = job_db.get_job(job_id)
        meta = job.get("metadata") or {} if job else {}
        if isinstance(meta, str):
            import json as _jm
            try:
                meta = _jm.loads(meta)
            except Exception:
                meta = {}
        if meta.get("job_mode") == "import":
            refinements = job_db.get_refinement_history(job_id, limit=1)
            prompt = refinements[0].get("prompt", "") if refinements else ""
            progress_callback("completed", 95, "Creating GitHub PR...")
            pr_url = _create_github_pr(Path(workspace_path), job_id, prompt, job_db)
            if pr_url:
                meta["pr_url"] = pr_url
                import json as _jmu
                job_db.update_job(job_id, {"metadata": _jmu.dumps(meta)})
    except Exception as e:
        logger.warning("Post-refinement PR step failed (non-fatal): %s", e)

    job_db.complete_refinement(refinement_id)
    job_db.update_job(job_id, {
        "status": previous_status,
        "current_phase": "completed",
        "progress": 100,
        "error": None,
    })
    done_msg = f"{message} — PR: {pr_url}" if pr_url else message
    progress_callback("completed", 100, done_msg)
    return {"status": "success", "pr_url": pr_url}
