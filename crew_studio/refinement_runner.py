"""
Refinement runner: git snapshot, context loading, RefinementAgent execution.
Runs in a thread; does not set WORKSPACE_PATH env (thread-safe).
"""
import logging
import os
import uuid
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

# Max characters to inline per file (prevent huge files from blowing the context)
_MAX_INLINE_CHARS = 8000

# Max total characters for all inlined files combined
_MAX_TOTAL_INLINE_CHARS = 50000


def _discover_source_files(workspace_path: Path) -> List[str]:
    """Recursively discover all source-code files in the workspace.
    Returns relative paths sorted alphabetically."""
    workspace_path = Path(workspace_path)
    results: List[str] = []
    for item in sorted(workspace_path.rglob("*")):
        if any(part in _SKIP_DIRS for part in item.parts):
            continue
        if item.is_file() and item.suffix.lower() in _SOURCE_EXTENSIONS:
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

    Returns:
        {"status": "success"} or {"status": "error", "error": "..."}.
    """
    workspace_path = Path(workspace_path)
    progress_callback("refining", 5, "Creating git snapshot...")
    _git_snapshot(workspace_path)

    progress_callback("refining", 10, "Loading context...")
    tech_stack_content = _load_tech_stack(workspace_path)
    file_listing = _load_file_listing(workspace_path)
    refinement_history = job_db.get_refinement_history(job_id, limit=10)
    # Exclude current (running) refinement from history context
    refinement_history = [r for r in refinement_history if r.get("id") != refinement_id]

    if file_path:
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
        )
    else:
        # ── PROJECT-WIDE SCOPE ────────────────────────────────────────
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
    return _complete_refinement(workspace_path, job_db, job_id, refinement_id, progress_callback, summary, previous_status)


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
) -> Dict[str, Any]:
    """Run refinement across ALL source files, one file at a time."""
    progress_callback("refining", 12, "Scanning project files...")
    source_files = _discover_source_files(workspace_path)
    if not source_files:
        return _fail_refinement(job_db, job_id, refinement_id, progress_callback,
                                "No source files found in the workspace.", previous_status)
    logger.info("Project-wide scope: %d source files to process: %s", len(source_files), source_files)
    progress_callback("refining", 15, f"Found {len(source_files)} source files to process...")

    files_modified: List[str] = []
    files_skipped: List[str] = []
    total = len(source_files)

    for idx, fp in enumerate(source_files):
        pct = 15 + int((idx / total) * 70)  # progress from 15% to 85%
        progress_callback("refining", pct, f"Processing file {idx + 1}/{total}: {fp}")

        file_content = _load_file_content(workspace_path, fp)
        if file_content is None:
            logger.warning("Skipping %s (could not read)", fp)
            files_skipped.append(fp)
            continue

        # Snapshot before this file so we can check if it changed
        _git_snapshot(workspace_path)

        try:
            from src.llamaindex_crew.agents.refinement_agent import RefinementAgent
            agent = RefinementAgent(workspace_path=workspace_path, project_id=job_id)
            agent_response = agent.run(
                user_prompt=prompt,
                file_path=fp,
                tech_stack_content=tech_stack_content,
                file_listing=file_listing,
                refinement_history=refinement_history,
                initial_file_content=file_content,
            )
            logger.info("Project-wide agent response for %s: %s", fp, str(agent_response)[:300])
        except Exception as e:
            logger.warning("Agent error on %s (continuing): %s", fp, e)
            files_skipped.append(fp)
            continue

        # Check if this file was modified or deleted (deletion may not show in git if file was untracked)
        file_was_deleted = not (workspace_path / fp).exists()
        if _workspace_has_changes(workspace_path) or file_was_deleted:
            files_modified.append(fp)
            logger.info("Modified or deleted: %s", fp)
        else:
            files_skipped.append(fp)
            logger.info("No changes for: %s", fp)

    # Final summary
    if not files_modified:
        error_msg = (
            f"Processed {total} files but no changes were written. "
            "The AI may not have found relevant changes to make. Try being more specific."
        )
        return _fail_refinement(job_db, job_id, refinement_id, progress_callback, error_msg, previous_status)

    summary = f"Refinement completed — modified {len(files_modified)}/{total} files: {', '.join(files_modified)}"
    return _complete_refinement(workspace_path, job_db, job_id, refinement_id, progress_callback, summary, previous_status)


# ── Helpers ────────────────────────────────────────────────────────────────────

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


def _complete_refinement(workspace_path, job_db, job_id, refinement_id, progress_callback, message, previous_status: str = "completed"):
    """Mark refinement as completed and restore job status so dashboard shows correct state."""
    try:
        _git_snapshot(workspace_path)
    except Exception as e:
        logger.warning("Post-refinement git snapshot failed: %s", e)

    job_db.complete_refinement(refinement_id)
    job_db.update_job(job_id, {
        "status": previous_status,
        "current_phase": "completed",
        "progress": 100,
        "error": None,
    })
    progress_callback("completed", 100, message)
    return {"status": "success"}
