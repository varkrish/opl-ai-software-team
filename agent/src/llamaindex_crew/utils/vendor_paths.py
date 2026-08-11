"""
One definition of "don't look in here", instead of eleven.

Ten places in this codebase each carried their own set of directories to skip,
no two alike — file_tools, tldr_tools, meta_agent, manifest_repair,
sandbox_client, refinement_context, refinement_impact, document_indexer and
wiring_contract twice. Adding an eleventh to fix the next bug is how the list
got to ten.

Two of them were not merely inconsistent but wrong:

  * ``document_indexer`` matched a *prefix*, so it caught ``node_modules/…`` at
    the workspace root and missed ``frontend/node_modules/…``.
  * ``wiring_contract`` matched a *substring* — ``"venv" in path`` — which also
    swallows a legitimate ``my-venv-tool/`` package.

Both are fixed here by matching whole path components.

Three concepts were tangled in those sets, which is why one flat union is the
wrong answer. A dependency scanner wants somebody else's *source*; it should
still see this project's build output as its own. A file walker wants to skip
all three. Keeping them separate lets each caller say what it means:

  VENDOR   someone else's source           node_modules, .venv, site-packages
  BUILD    output this project generates   dist, target, __pycache__
  TOOLING  vcs and editor state            .git, .tldr, .idea

Stdlib only and imported by nothing, so any module — including crew_studio —
can use it without risking an import cycle.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Optional, Union

# Third-party source that happens to sit in the tree. Never this project's code,
# so it must not be indexed, scanned for symbols, or turned into a package.
VENDOR_DIRS: frozenset = frozenset({
    "node_modules",
    "bower_components",
    "jspm_packages",
    ".venv",
    "venv",
    "virtualenv",
    "site-packages",
    "vendor",
    ".bundle",
    "Pods",
})

# Output this project generates. Skipped when walking, but deliberately *not*
# "vendored": a stale class file under target/ is our own, and treating it as a
# third-party artifact hid the fact that it should have been cleaned.
BUILD_DIRS: frozenset = frozenset({
    "dist",
    "build",
    "target",
    "out",
    "bin",
    "obj",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    ".gradle",
    ".next",
    ".nuxt",
    ".parcel-cache",
    "htmlcov",
    ".coverage",
})

# Version control, editor and tool state.
TOOLING_DIRS: frozenset = frozenset({
    ".git",
    ".hg",
    ".svn",
    ".tldr",
    ".idea",
    ".vscode",
    ".DS_Store",
})

# The default for anything walking a workspace.
SKIP_DIRS: frozenset = VENDOR_DIRS | BUILD_DIRS | TOOLING_DIRS

PathLike = Union[str, Path, None]


def _components(path: PathLike) -> List[str]:
    """Path components, tolerating Windows separators and absolute paths."""
    if not path:
        return []
    text = str(path).replace("\\", "/")
    return [part for part in text.split("/") if part not in ("", ".")]


def _matches(path: PathLike, names: Iterable[str]) -> bool:
    lookup = set(names)
    return any(part in lookup for part in _components(path))


def is_vendored(path: PathLike) -> bool:
    """True when any component of *path* is third-party source.

    Whole components only. ``my-venv-tool/main.py`` is project source, and the
    substring check it replaces called it a virtualenv.
    """
    return _matches(path, VENDOR_DIRS)


def is_build_output(path: PathLike) -> bool:
    """True when any component of *path* is generated output."""
    return _matches(path, BUILD_DIRS)


def is_skippable(path: PathLike) -> bool:
    """True for anything a workspace walk should not descend into."""
    return _matches(path, SKIP_DIRS)


def prune_dirnames(dirnames: List[str], extra: Optional[Iterable[str]] = None) -> List[str]:
    """Drop skippable entries from an ``os.walk`` dirnames list, in place.

    ``os.walk`` only honours pruning done to the list object it handed you, so
    this mutates rather than returning a copy. The list is returned as well for
    call sites that prefer to read as an expression.
    """
    skip = set(SKIP_DIRS)
    if extra:
        skip |= set(extra)
    dirnames[:] = [d for d in dirnames if d not in skip]
    return dirnames
