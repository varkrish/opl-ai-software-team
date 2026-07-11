"""
Tech-stack manifest guard — feature toggle for file_writer allowlists and validation.

Controlled by ``TECH_STACK_MANIFEST_GUARD``:

- ``strict``  — only registered tech-stack paths may be written during remediation;
                file_manifest validation flags any other source file.
- ``relaxed`` — default; remediation may write registered paths, on-disk sources,
                and companion test paths; validation uses the same expanded set.
- ``off``     — no write-time allowlist during remediation; file_manifest check skipped.
"""
from __future__ import annotations

import logging
import os
from enum import Enum
from pathlib import Path
from typing import Optional, Set

logger = logging.getLogger(__name__)

_ENV_VAR = "TECH_STACK_MANIFEST_GUARD"

from .test_companion import is_test_file_path


class ManifestGuardMode(str, Enum):
    STRICT = "strict"
    RELAXED = "relaxed"
    OFF = "off"


def get_manifest_guard_mode() -> ManifestGuardMode:
    raw = os.environ.get(_ENV_VAR, "relaxed").lower().strip()
    try:
        return ManifestGuardMode(raw)
    except ValueError:
        logger.warning(
            "Invalid %s=%r — falling back to relaxed (use strict|relaxed|off)",
            _ENV_VAR,
            raw,
        )
        return ManifestGuardMode.RELAXED


def companion_test_paths(source_path: str) -> list[str]:
    """Return conventional test file paths colocated with a source file."""
    if is_test_file_path(source_path):
        return []
    p = Path(source_path)
    stem = p.stem
    ext = p.suffix or ".ts"
    parent = p.parent.as_posix()
    if parent == ".":
        parent = ""
    base = f"{parent}/" if parent else ""
    candidates = [
        f"{base}__tests__/{stem}.test{ext}",
        f"{base}{stem}.test{ext}",
        f"{base}{stem}.spec{ext}",
        f"{base}test_{stem}{ext}",
        f"{base}{stem}_test{ext}",
        f"tests/{stem}.test{ext}",
        f"tests/test_{stem}{ext}",
        f"test/{stem}.test{ext}",
    ]
    if ext.lower() == ".java":
        candidates.append(f"{base}{stem}Test.java")
    if ext.lower() == ".kt":
        candidates.append(f"{base}{stem}Test.kt")
    return candidates


def expand_python_package_inits(paths: Set[str]) -> Set[str]:
    """Add ``<pkg>/__init__.py`` for registered Python modules in subpackages.

    Mirrors ``TaskManager._inject_init_py_tasks``: every ``.py`` file inside a
    directory implies that directory is a package and needs an ``__init__.py``,
    unless a same-named flat module (``dir.py``) is already registered.
    """
    expanded: Set[str] = set(paths)
    for fp in paths:
        if not fp.endswith(".py") or fp.endswith("/__init__.py"):
            continue
        parts = Path(fp).parts
        if len(parts) < 2:
            continue
        for depth in range(1, len(parts)):
            dir_path = "/".join(parts[:depth])
            init_path = f"{dir_path}/__init__.py"
            flat_module = f"{dir_path}.py"
            if flat_module in paths:
                continue
            expanded.add(init_path)
    return expanded


def is_companion_python_init(file_path: str, allowed: Set[str]) -> bool:
    """True when *file_path* is a package ``__init__.py`` implied by *allowed* modules."""
    if not file_path.endswith("/__init__.py"):
        return False
    pkg = file_path[: -len("/__init__.py")]
    if f"{pkg}.py" in allowed:
        return False
    prefix = f"{pkg}/"
    return any(
        p.startswith(prefix)
        and p.endswith(".py")
        and not p.endswith("/__init__.py")
        for p in allowed
    )


def dev_phase_write_allowlist(registered: Set[str]) -> Set[str]:
    """Strict dev-phase allowlist: registered paths plus implied package inits."""
    expanded = expand_python_package_inits(registered)
    added = len(expanded) - len(registered)
    if added:
        logger.info(
            "Manifest guard: dev allowlist expanded with %d package __init__.py path(s)",
            added,
        )
    return expanded


def expand_remediation_paths(allowed: Set[str], workspace: Path) -> Set[str]:
    """Registered paths plus on-disk sources and companion test paths."""
    expanded: Set[str] = set(allowed)
    seeds: Set[str] = set(allowed)
    skip_prefixes = (".", "state_", "tasks_", "features/")
    skip_suffixes = (".md", ".json", ".yaml", ".yml", ".log")
    for src in workspace.rglob("*"):
        if not src.is_file():
            continue
        rel = str(src.relative_to(workspace))
        if any(rel.startswith(p) for p in skip_prefixes):
            continue
        if rel.endswith(skip_suffixes):
            continue
        seeds.add(rel)
    for path in seeds:
        expanded.add(path)
        expanded.update(companion_test_paths(path))
    return expanded


def is_companion_test_file(rel: str, registered: Set[str]) -> bool:
    """True when *rel* is a test file paired with a registered source path."""
    if not is_test_file_path(rel):
        return False
    from .test_companion import resolve_companion_source
    return resolve_companion_source(rel, registered) is not None


def effective_manifest_paths(
    registered: Set[str],
    workspace: Path,
    mode: Optional[ManifestGuardMode] = None,
) -> Optional[Set[str]]:
    """Paths treated as authorized for validation; ``None`` when check is skipped."""
    mode = mode or get_manifest_guard_mode()
    if mode == ManifestGuardMode.OFF:
        return None
    if mode == ManifestGuardMode.STRICT:
        return expand_python_package_inits(registered)
    return expand_remediation_paths(registered, workspace)


def remediation_write_allowlist(
    registered: Set[str],
    workspace: Path,
    mode: Optional[ManifestGuardMode] = None,
) -> Optional[Set[str]]:
    """Allowlist for ``file_writer`` during post-build remediation.

    Returns ``None`` to disable the write guard (``off`` mode).
    """
    mode = mode or get_manifest_guard_mode()
    if mode == ManifestGuardMode.OFF:
        return None
    if mode == ManifestGuardMode.STRICT:
        return dev_phase_write_allowlist(registered)
    expanded = expand_remediation_paths(registered, workspace)
    logger.info(
        "Manifest guard [%s]: remediation allowlist %d paths (%d registered)",
        mode.value,
        len(expanded),
        len(registered),
    )
    return expanded


def is_path_manifest_authorized(
    rel: str,
    registered: Set[str],
    workspace: Path,
    mode: Optional[ManifestGuardMode] = None,
) -> bool:
    """Whether *rel* passes the file_manifest validation check."""
    mode = mode or get_manifest_guard_mode()
    if mode == ManifestGuardMode.OFF:
        return True
    effective = effective_manifest_paths(registered, workspace, mode)
    assert effective is not None
    if rel in effective:
        return True
    if mode == ManifestGuardMode.RELAXED and is_companion_test_file(rel, registered):
        return True
    return False


def dev_phase_write_guard_enabled(mode: Optional[ManifestGuardMode] = None) -> bool:
    """Whether to set a brief allowlist at the start of the development phase."""
    mode = mode or get_manifest_guard_mode()
    return mode == ManifestGuardMode.STRICT
