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
import re
from enum import Enum
from pathlib import Path
from typing import Optional, Set

logger = logging.getLogger(__name__)

_ENV_VAR = "TECH_STACK_MANIFEST_GUARD"

_TEST_PATH_RE = re.compile(
    r"(?:test[_/]|[_/]test\.|\.test\.|\.spec\.|__tests__|tests/|spec/)",
    re.IGNORECASE,
)


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
    if _TEST_PATH_RE.search(source_path):
        return []
    p = Path(source_path)
    stem = p.stem
    ext = p.suffix or ".ts"
    parent = p.parent.as_posix()
    if parent == ".":
        parent = ""
    base = f"{parent}/" if parent else ""
    return [
        f"{base}__tests__/{stem}.test{ext}",
        f"{base}{stem}.test{ext}",
        f"{base}{stem}.spec{ext}",
        f"tests/{stem}.test{ext}",
        f"test/{stem}.test{ext}",
    ]


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
    if not _TEST_PATH_RE.search(rel):
        return False
    stem = Path(rel).stem
    for suffix in (".test", ".spec"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    ext = Path(rel).suffix
    for src in registered:
        if Path(src).stem == stem and Path(src).suffix == ext:
            return True
    return False


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
        return set(registered)
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
        return set(registered)
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
