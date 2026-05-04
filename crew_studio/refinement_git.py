"""
Git helpers for refinement change summaries (MTA migration/changes–compatible JSON).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    import git as gitpython
except ImportError:
    gitpython = None


def refinement_baseline_and_head(repo: "gitpython.Repo") -> Tuple[Any, Any]:
    """Oldest commit vs HEAD — cumulative diff across all completed refinements."""
    commits = list(repo.iter_commits())
    if len(commits) < 2:
        raise ValueError("not_enough_commits")
    # iter_commits() is newest-first; oldest root is last
    return commits[-1], repo.head.commit


def compute_refinement_changes(ws: Path) -> Dict[str, Any]:
    """
    Build the same shape as GET /migration/changes for the Files UI.

    Baseline = repository root (first snapshot before any refinement agent run).
    Head = HEAD (workspace after refinements).
    """
    empty: Dict[str, Any] = {
        "job_id": "",
        "baseline_commit": "",
        "head_commit": "",
        "total_files": 0,
        "total_insertions": 0,
        "total_deletions": 0,
        "files": [],
    }

    if not gitpython:
        empty["error"] = "gitpython not installed"
        return empty

    ws = Path(ws)
    if not ws.is_dir() or not (ws / ".git").exists():
        return empty

    try:
        repo = gitpython.Repo(ws)
        try:
            baseline, head = refinement_baseline_and_head(repo)
        except ValueError:
            return empty

        diff_index = baseline.diff(head, create_patch=False)
        files_changed: List[Dict[str, Any]] = []

        for diff_item in diff_index:
            file_path = diff_item.b_path or diff_item.a_path
            files_changed.append({
                "path": file_path,
                "change_type": diff_item.change_type,
                "insertions": 0,
                "deletions": 0,
            })

        total_insertions = 0
        total_deletions = 0
        try:
            stat_output = repo.git.diff(
                baseline.hexsha, head.hexsha, stat=True, numstat=True
            )
            stat_lookup: Dict[str, Tuple[int, int]] = {}
            for line in stat_output.strip().split("\n"):
                parts = line.split("\t")
                if len(parts) == 3:
                    try:
                        ins = int(parts[0]) if parts[0] != "-" else 0
                        dels = int(parts[1]) if parts[1] != "-" else 0
                        stat_lookup[parts[2]] = (ins, dels)
                    except ValueError:
                        pass
            for f in files_changed:
                p = f["path"]
                if p in stat_lookup:
                    f["insertions"], f["deletions"] = stat_lookup[p]
                total_insertions += f["insertions"]
                total_deletions += f["deletions"]
        except Exception as e:
            logger.warning("refinement changes numstat failed: %s", e)

        return {
            "job_id": "",
            "baseline_commit": baseline.hexsha[:7],
            "head_commit": head.hexsha[:7],
            "total_files": len(files_changed),
            "total_insertions": total_insertions,
            "total_deletions": total_deletions,
            "files": sorted(files_changed, key=lambda x: x["path"]),
        }
    except Exception as e:
        logger.error("compute_refinement_changes failed: %s", e)
        empty["error"] = str(e)
        return empty


def read_blob_at_rev(repo: "gitpython.Repo", rev_hex: str, rel_path: str) -> Optional[str]:
    """Return file text at rev:path, or None if missing / binary / error."""
    rel_path = rel_path.replace("\\", "/").strip("/")
    if not rel_path or ".." in rel_path.split("/"):
        return None
    try:
        out = repo.git.show(f"{rev_hex}:{rel_path}")
        return out
    except Exception:
        return None


def compare_refinement_file(ws: Path, rel_path: str) -> Dict[str, Any]:
    """Original (baseline root) vs modified (HEAD) for Monaco diff."""
    if not gitpython:
        return {"error": "gitpython not installed", "path": rel_path, "original": "", "modified": ""}

    ws = Path(ws)
    if not ws.is_dir() or not (ws / ".git").exists():
        return {"error": "No git history in workspace", "path": rel_path, "original": "", "modified": ""}

    try:
        repo = gitpython.Repo(ws)
        baseline, head = refinement_baseline_and_head(repo)
    except ValueError:
        return {"error": "Not enough git commits for diff (need at least one completed refinement).", "path": rel_path, "original": "", "modified": ""}
    except Exception as e:
        return {"error": str(e), "path": rel_path, "original": "", "modified": ""}

    original = read_blob_at_rev(repo, baseline.hexsha, rel_path)
    if original is None:
        original = ""
    modified = read_blob_at_rev(repo, head.hexsha, rel_path)
    if modified is None:
        modified = ""

    return {
        "path": rel_path,
        "baseline_commit": baseline.hexsha[:7],
        "head_commit": head.hexsha[:7],
        "original": original,
        "modified": modified,
    }
