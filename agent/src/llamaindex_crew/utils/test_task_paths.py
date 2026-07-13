"""
Derive test file paths for TDD pipelines when tech_stack.md omits a tests/ tree.

Used by TaskManager.register_tdd_test_tasks to register file_creation tasks
before the QA phase materializes them.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, List, Optional, Set

from .test_companion import is_test_file_path

# Stems we do not mirror into standalone test modules.
_SKIP_SOURCE_STEMS = frozenset({"__init__", "prompts", "conftest"})

# Directories whose files should not generate mirrored tests.
_SKIP_DIR_SEGMENTS = frozenset({"helm", "templates", ".github", ".gitlab"})

# Extensions eligible for mirrored test modules.
_TESTABLE_SOURCE_SUFFIXES = frozenset({".py", ".ts", ".tsx", ".js", ".jsx"})

# Paths explicitly mentioned in test_plan.md (backticks or plain text).
_TEST_PATH_IN_PLAN_RE = re.compile(
    r"(?:[`'\"(]|^|\s)(tests/[\w\-./]+\.(?:py|ts|tsx|js|jsx|json))(?:[`'\")]|\s|$)",
    re.IGNORECASE | re.MULTILINE,
)

# Standard pytest scaffolding when any Python source is present.
_PYTEST_SCAFFOLD = (
    "tests/conftest.py",
    "tests/__init__.py",
)


def extract_test_paths_from_plan(test_plan_content: str) -> List[str]:
    """Return deduplicated test/fixture paths referenced in test_plan.md."""
    if not test_plan_content:
        return []
    seen: Set[str] = set()
    out: List[str] = []
    for match in _TEST_PATH_IN_PLAN_RE.finditer(test_plan_content):
        path = match.group(1).replace("\\", "/").lstrip("./")
        if path and path not in seen and is_test_file_path(path):
            seen.add(path)
            out.append(path)
    return out


def derive_mirror_test_path(source_path: str) -> Optional[str]:
    """Map a source module path to a conventional mirrored test path."""
    if not source_path:
        return None
    norm = source_path.replace("\\", "/").lstrip("./")
    if is_test_file_path(norm):
        return None

    parts_lower = norm.lower().split("/")
    if any(seg in _SKIP_DIR_SEGMENTS for seg in parts_lower):
        return None

    path = Path(norm)
    suffix = path.suffix.lower()
    if suffix not in _TESTABLE_SOURCE_SUFFIXES:
        return None
    if path.stem in _SKIP_SOURCE_STEMS:
        return None

    stem = path.stem
    parent_name = path.parent.name if path.parent != Path(".") else ""
    parent_path = str(path.parent).replace("\\", "/") if path.parent != Path(".") else ""

    # agents/place_search/agent.py -> tests/agents/test_place_search.py
    if stem.lower() in ("agent", "main", "app") and parent_name:
        if parent_path.startswith("agents/"):
            return f"tests/agents/test_{parent_name}.py"
        if parent_path:
            return f"tests/{parent_path}/test_{stem}.py"
        return f"tests/test_{stem}.py"

    if parent_path:
        return f"tests/{parent_path}/test_{stem}{suffix}"
    return f"tests/test_{stem}{suffix}"


def derive_tdd_test_paths(
    source_paths: Iterable[str],
    test_plan_content: str = "",
    *,
    include_pytest_scaffold: bool = True,
) -> List[str]:
    """Combine mirrored source paths, plan references, and pytest scaffold."""
    seen: Set[str] = set()
    ordered: List[str] = []

    def _add(path: str) -> None:
        norm = path.replace("\\", "/").lstrip("./")
        if not norm or norm in seen:
            return
        if not is_test_file_path(norm):
            return
        seen.add(norm)
        ordered.append(norm)

    for raw in source_paths:
        mirrored = derive_mirror_test_path(raw)
        if mirrored:
            _add(mirrored)

    for path in extract_test_paths_from_plan(test_plan_content):
        _add(path)

    has_python_source = any(
        p.replace("\\", "/").lower().endswith(".py")
        and not is_test_file_path(p)
        for p in source_paths
    )
    if include_pytest_scaffold and has_python_source:
        for scaffold in _PYTEST_SCAFFOLD:
            _add(scaffold)

    return ordered
