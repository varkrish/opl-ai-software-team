"""
Migration runner: two-phase orchestration (analysis → execution).
Runs in a background thread; does not set env vars (thread-safe).
"""
import json
import logging
import re
import subprocess
import uuid
from pathlib import Path
from typing import Callable, Optional, Dict, Any, List

from .utils import git_snapshot, workspace_has_changes, load_migration_rules

logger = logging.getLogger(__name__)

# Max chars to inline per file in execution prompt
_MAX_INLINE_CHARS = 8_000


def _normalize_path_for_workspace(ws: Path, path: str) -> str:
    """Return path that exists in workspace, or original. Try stripping leading app/."""
    if (ws / path).is_file():
        return path
    if path.startswith("app/"):
        alt = path[4:]
        if (ws / alt).is_file():
            return alt
    return path


def _find_java_files_containing(ws: Path, pattern: str) -> List[str]:
    """Find relative paths of .java files under ws that contain the given string."""
    out: List[str] = []
    try:
        result = subprocess.run(
            ["grep", "-rl", "--include=*.java", pattern, str(ws)],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(ws),
        )
        if result.returncode == 0 and result.stdout:
            for line in result.stdout.strip().splitlines():
                p = Path(line)
                try:
                    rel = p.relative_to(ws)
                    out.append(str(rel).replace("\\", "/"))
                except ValueError:
                    pass
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.warning("Could not grep for Java files: %s", e)
    return out


def _extract_javax_patterns_from_issue(issue: Dict[str, Any]) -> List[str]:
    """Extract ALL javax.* patterns to search for (e.g. javax.persistence, javax.ws.rs)."""
    text = (issue.get("title") or "") + " " + (issue.get("migration_hint") or "")
    # e.g. "Replace the `javax.persistence` import" or "javax.persistence has been replaced"
    matches = re.findall(r"javax\.[a-z.]+", text, re.I)
    # Deduplicate while preserving order
    seen: set = set()
    unique: List[str] = []
    for m in matches:
        # Trim trailing dots
        m = m.rstrip(".")
        if m not in seen and len(m) > 6:  # "javax." alone is too short
            seen.add(m)
            unique.append(m)
    return unique


def _find_file_by_basename(ws: Path, basename: str) -> Optional[str]:
    """Search workspace for a file by its basename (e.g. pom.xml → pom.xml at root)."""
    # Try common locations first (fast)
    for candidate in [basename, f"src/{basename}", f"src/main/{basename}"]:
        if (ws / candidate).is_file():
            return candidate
    # Walk workspace (limited depth to avoid huge scans)
    try:
        result = subprocess.run(
            ["find", str(ws), "-maxdepth", "5", "-name", basename, "-type", "f"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            first = result.stdout.strip().splitlines()[0]
            try:
                return str(Path(first).relative_to(ws)).replace("\\", "/")
            except ValueError:
                pass
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def _expand_issues_to_workspace(ws: Path, issues: List[Dict[str, Any]]) -> None:
    """
    Mutate issues so file paths resolve in workspace:
    - Normalize paths (strip app/ prefix).
    - For missing .java paths, grep workspace for ALL javax.* patterns from the issue.
    - For missing non-.java paths, search workspace by basename (e.g. pom.xml).
    """
    for issue in issues:
        files = list(issue.get("files") or [])
        resolved = []
        needs_java_search = False

        for fpath in files:
            normalized = _normalize_path_for_workspace(ws, fpath)
            if (ws / normalized).is_file():
                if normalized not in resolved:
                    resolved.append(normalized)
                continue

            # Path doesn't exist — try alternatives
            if fpath.endswith(".java"):
                needs_java_search = True
            else:
                # Non-.java file: try finding by basename (e.g. pom.xml)
                basename = Path(fpath).name
                found = _find_file_by_basename(ws, basename)
                if found and found not in resolved:
                    resolved.append(found)
                    continue
                # If basename search failed and it looks like a misresolved .java
                if fpath.endswith(".java"):
                    needs_java_search = True

        # Grep workspace for ALL javax.* patterns from this issue
        if needs_java_search:
            patterns = _extract_javax_patterns_from_issue(issue)
            already_found: set = set(resolved)
            for pattern in patterns:
                found_files = _find_java_files_containing(ws, pattern)
                for p in found_files:
                    if p not in already_found:
                        resolved.append(p)
                        already_found.add(p)

        if resolved:
            issue["files"] = resolved
        # else: leave files as-is so DB still has original; they'll be skipped


# javax.* packages that moved to jakarta.* in Jakarta EE 9+
_JAVAX_TO_JAKARTA_PACKAGES = [
    "javax.annotation",
    "javax.batch",
    "javax.decorator",
    "javax.ejb",
    "javax.el",
    "javax.enterprise",
    "javax.faces",
    "javax.inject",
    "javax.interceptor",
    "javax.jms",
    "javax.json",
    "javax.json.bind",
    "javax.mail",
    "javax.persistence",
    "javax.resource",
    "javax.security.auth.message",
    "javax.security.enterprise",
    "javax.security.jacc",
    "javax.servlet",
    "javax.transaction",
    "javax.validation",
    "javax.websocket",
    "javax.ws.rs",
    "javax.xml.bind",
    "javax.xml.soap",
    "javax.xml.ws",
]


def _javax_to_jakarta_sweep(ws: Path) -> int:
    """Deterministic find-and-replace of javax→jakarta in all Java/XML files.
    
    Handles: import statements, XML property names, XML namespace references.
    Returns the number of files modified.
    """
    modified = 0
    
    # Sort longest-first so javax.json.bind matches before javax.json
    replacements = sorted(_JAVAX_TO_JAKARTA_PACKAGES, key=len, reverse=True)
    
    # Process .java files
    java_files = list(ws.rglob("*.java"))
    for jf in java_files:
        try:
            content = jf.read_text(encoding="utf-8", errors="replace")
            new_content = content
            for pkg in replacements:
                jakarta_pkg = pkg.replace("javax.", "jakarta.", 1)
                new_content = new_content.replace(pkg, jakarta_pkg)
            if new_content != content:
                jf.write_text(new_content, encoding="utf-8")
                modified += 1
        except (OSError, UnicodeDecodeError) as e:
            logger.warning("Sweep: could not process %s: %s", jf, e)
    
    # Process XML files (persistence.xml, web.xml properties, etc.)
    xml_files = list(ws.rglob("*.xml"))
    for xf in xml_files:
        try:
            content = xf.read_text(encoding="utf-8", errors="replace")
            new_content = content
            for pkg in replacements:
                jakarta_pkg = pkg.replace("javax.", "jakarta.", 1)
                new_content = new_content.replace(pkg, jakarta_pkg)
            # Also handle XML namespace migrations
            new_content = new_content.replace(
                "http://xmlns.jcp.org/xml/ns/javaee",
                "https://jakarta.ee/xml/ns/jakartaee",
            )
            new_content = new_content.replace(
                "http://xmlns.jcp.org/xml/ns/persistence",
                "https://jakarta.ee/xml/ns/persistence",
            )
            # XSD version updates
            new_content = new_content.replace("persistence_2_1.xsd", "persistence_3_0.xsd")
            new_content = new_content.replace('version="2.1"', 'version="3.0"')
            new_content = new_content.replace("beans_1_1.xsd", "beans_3_0.xsd")
            if new_content != content:
                xf.write_text(new_content, encoding="utf-8")
                modified += 1
        except (OSError, UnicodeDecodeError) as e:
            logger.warning("Sweep: could not process %s: %s", xf, e)
    
    return modified


def run_migration(
    job_id: str,
    workspace_path: str,
    migration_goal: str,
    report_path: str,
    migration_notes: Optional[str],
    job_db: Any,
    progress_callback: Optional[Callable] = None,
) -> None:
    """
    Run a full migration: analyse MTA report → apply changes per file.

    Args:
        job_id: Parent job ID.
        workspace_path: Absolute path to the job workspace.
        migration_goal: High-level migration description.
        report_path: Relative path to the MTA report inside workspace/docs/.
        migration_notes: Optional Tier 4 per-run notes.
        job_db: JobDatabase instance.
        progress_callback: Optional fn(phase, progress_pct, message).
    """
    ws = Path(workspace_path)
    migration_id = f"mig-{uuid.uuid4().hex[:12]}"

    def _progress(phase: str, pct: int, msg: str):
        if progress_callback:
            try:
                progress_callback(phase, pct, msg)
            except Exception:
                pass

    try:
        # ── Phase 0: Prepare ──────────────────────────────────────────
        _progress("migrating", 5, "Creating pre-migration snapshot...")
        git_snapshot(ws, "pre-migration snapshot")

        # Load Tier 2: convention rules
        repo_rules = load_migration_rules(ws)

        # ── Phase 1: Analysis ─────────────────────────────────────────
        # Try deterministic MTA parsing first (fast path, no LLM)
        report_abs = ws / report_path
        from crew_studio.migration.mta_parser import is_mta_issues_json, parse_mta_issues_json
        
        if is_mta_issues_json(report_abs):
            _progress("parsing", 8, "Detected MTA issues.json format")
            logger.info("MTA issues.json detected — using deterministic parser (skipping LLM)")
            
            _progress("parsing", 10, "Parsing MTA report (fast path, no LLM)...")
            
            # Look for files.json in same directory for path resolution
            files_json_path = report_abs.parent / "files.json"
            if not files_json_path.is_file():
                files_json_path = None
            else:
                _progress("parsing", 12, "Found files.json for path resolution")
            
            issues = parse_mta_issues_json(report_abs, files_json_path)
            
            _progress("parsing", 20, f"Parsed {len(issues)} unique actionable issues")
            logger.info(f"Parsed {len(issues)} unique actionable issues from MTA report")
        else:
            # Fallback: use LLM analysis agent for non-MTA reports (CSV, HTML, YAML, text)
            _progress("analyzing", 8, "Detected non-MTA report format")
            _progress("analyzing", 10, "Analyzing report with AI (this may take a few minutes)...")
            logger.info("Non-MTA report format — using MigrationAnalysisAgent")
            from llamaindex_crew.agents.migration_agent import MigrationAnalysisAgent

            # Build file listing for context
            from llamaindex_crew.tools.file_tools import file_lister
            file_listing = file_lister(".", workspace_path=str(ws))

            analysis_agent = MigrationAnalysisAgent(ws, job_id)
            analysis_agent.run(
                report_path=report_path,
                migration_goal=migration_goal,
                file_listing=file_listing,
                user_notes=migration_notes,
            )

            # Parse the plan written by the agent
            plan_path = ws / "migration_plan.json"
            if not plan_path.exists():
                raise RuntimeError("Analysis agent did not produce migration_plan.json")

            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            issues = plan.get("issues", [])
            if not issues:
                raise RuntimeError("Migration plan has no issues")

        # Resolve paths to workspace: normalize app/, expand missing .java by pattern
        _expand_issues_to_workspace(ws, issues)

        # Write migration_plan.json for audit trail (AFTER expansion so paths are correct)
        from datetime import datetime as _dt
        plan = {
            "migration_goal": migration_goal,
            "source_report": report_path,
            "total_issues": len(issues),
            "issues": issues,
            "parser": "deterministic_mta_parser" if is_mta_issues_json(report_abs) else "llm_analysis",
            "resolved_at": _dt.now().isoformat(),
        }
        plan_path = ws / "migration_plan.json"
        plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
        _progress("parsing", 25, f"Migration plan ready: {len(issues)} issues")

        # Store issues in DB (id must be unique across all migrations, not just rule_id)
        for issue in issues:
            raw_id = issue.get("id", uuid.uuid4().hex[:8])
            unique_issue_id = f"{migration_id}-{raw_id}"
            issue["id"] = unique_issue_id
            job_db.create_migration_issue(
                issue_id=unique_issue_id,
                job_id=job_id,
                migration_id=migration_id,
                title=issue.get("title", ""),
                severity=issue.get("severity", "optional"),
                effort=issue.get("effort", "medium"),
                files=issue.get("files", []),
                description=issue.get("description", ""),
                migration_hint=issue.get("migration_hint", ""),
            )

        _progress("migrating", 30, f"Plan created with {len(issues)} issues. Applying changes...")

        # ── Phase 2: Execution ────────────────────────────────────────
        from llamaindex_crew.agents.migration_agent import MigrationExecutionAgent

        # Group issues by file
        file_issues: Dict[str, List[Dict]] = {}
        for issue in issues:
            for fpath in issue.get("files", []):
                file_issues.setdefault(fpath, []).append(issue)

        total_files = len(file_issues)
        completed_files = 0

        for file_path, file_issue_list in file_issues.items():
            # Resolve path (e.g. app/pom.xml -> pom.xml)
            file_path = _normalize_path_for_workspace(ws, file_path)
            abs_path = ws / file_path
            issue_ids = [i.get("id", "?") for i in file_issue_list]
            logger.info("Processing file %s (%d issues: %s)", file_path, len(file_issue_list), issue_ids)

            # Mark issues as running
            for issue in file_issue_list:
                job_db.update_migration_issue_status(issue["id"], "running")

            if not abs_path.is_file():
                logger.warning("File not found, skipping: %s", abs_path)
                for issue in file_issue_list:
                    job_db.update_migration_issue_status(
                        issue["id"], "skipped", error="File not found"
                    )
                continue

            MAX_RETRIES = 2
            file_content = abs_path.read_text(encoding="utf-8", errors="replace")[
                :_MAX_INLINE_CHARS
            ]
            last_error = None

            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    exec_agent = MigrationExecutionAgent(ws, job_id)
                    exec_agent.run(
                        file_path=file_path,
                        file_content=file_content,
                        issues=file_issue_list,
                        migration_goal=migration_goal,
                        repo_rules=repo_rules,
                        user_notes=migration_notes,
                    )

                    # Check if the agent actually changed anything
                    has_changes = workspace_has_changes(ws)
                    logger.info("Agent finished for %s (attempt %d) — has_changes=%s", file_path, attempt, has_changes)
                    if has_changes:
                        # Validate write: reject obviously corrupted output
                        new_content = abs_path.read_text(encoding="utf-8", errors="replace")
                        orig_len = len(file_content)
                        new_len = len(new_content)
                        # Reject if new content is < 30% of original (truncation)
                        if orig_len > 100 and new_len < 0.3 * orig_len:
                            raise ValueError(
                                f"Written file too small ({new_len} chars vs {orig_len}), possible truncation"
                            )
                        # Reject if file became a single broken line (LLM corruption)
                        if new_len > 0 and new_content.count("\n") == 0 and orig_len > 200:
                            raise ValueError("File was collapsed to a single line (corrupted)")

                        git_snapshot(ws, f"migration: {file_path}")
                        for issue in file_issue_list:
                            job_db.update_migration_issue_status(issue["id"], "completed")
                        last_error = None
                        break  # Success
                    else:
                        logger.info("No changes for %s (attempt %d) — marking completed", file_path, attempt)
                        for issue in file_issue_list:
                            job_db.update_migration_issue_status(issue["id"], "completed")
                        last_error = None
                        break  # No change needed

                except (ValueError, OSError) as validation_err:
                    last_error = validation_err
                    logger.warning("Validation failed for %s (attempt %d/%d): %s", file_path, attempt, MAX_RETRIES, validation_err)
                    # Revert corrupted write
                    try:
                        subprocess.run(
                            ["git", "checkout", "--", file_path],
                            cwd=str(ws), capture_output=True, check=True,
                        )
                    except subprocess.CalledProcessError:
                        pass
                    if attempt < MAX_RETRIES:
                        logger.info("Retrying %s (attempt %d/%d)...", file_path, attempt + 1, MAX_RETRIES)
                        continue
                    # Final attempt failed
                    for issue in file_issue_list:
                        job_db.update_migration_issue_status(
                            issue["id"], "failed",
                            error=f"Output validation failed after {MAX_RETRIES} attempts: {validation_err!s}"[:500],
                        )
                except Exception as e:
                    last_error = e
                    logger.error("Migration failed for %s (attempt %d): %s", file_path, attempt, e)
                    if attempt < MAX_RETRIES:
                        # Revert and retry
                        try:
                            subprocess.run(
                                ["git", "checkout", "--", file_path],
                                cwd=str(ws), capture_output=True, check=True,
                            )
                        except subprocess.CalledProcessError:
                            pass
                        continue
                    for issue in file_issue_list:
                        job_db.update_migration_issue_status(
                            issue["id"], "failed", error=str(e)[:500]
                        )

            completed_files += 1
            pct = 30 + int(70 * completed_files / max(total_files, 1))
            _progress("migrating", pct, f"Processed {file_path} ({completed_files}/{total_files})")

        # ── Phase 3: Deterministic javax→jakarta sweep ─────────────────
        # The LLM agent may miss some javax imports (ws.rs, json, validation, etc.)
        # because MTA reports don't always cover all packages.
        # This sweep catches everything the LLM missed.
        if "jakarta" in migration_goal.lower() or "javax" in migration_goal.lower():
            _progress("migrating", 95, "Running deterministic javax→jakarta sweep...")
            sweep_count = _javax_to_jakarta_sweep(ws)
            if sweep_count > 0:
                logger.info("Deterministic sweep updated %d files", sweep_count)
                git_snapshot(ws, "deterministic javax→jakarta sweep")
            _progress("migrating", 98, f"Sweep updated {sweep_count} additional files")

        # ── Done ──────────────────────────────────────────────────────
        git_snapshot(ws, "post-migration snapshot")
        _progress("completed", 100, "Migration complete")

    except Exception as e:
        logger.error("Migration run failed for job %s: %s", job_id, e, exc_info=True)
        _progress("migration_failed", 0, str(e))
        raise
