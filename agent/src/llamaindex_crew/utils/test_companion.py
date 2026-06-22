"""
Language-agnostic pairing between test files and their companion source modules.

Supports common conventions across Python (pytest/Frappe), Java/Kotlin (JUnit),
and JS/TS (Jest/Vitest) without hard-coding a single language.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, List, Optional

# Shared with manifest_guard — keep in sync
TEST_PATH_RE = re.compile(
    r"(?:test[_/]|[_/]test\.|\.test\.|\.spec\.|__tests__|tests/|spec/)",
    re.IGNORECASE,
)


def is_test_file_path(path: str) -> bool:
    if not path:
        return False
    norm = path.replace("\\", "/")
    if TEST_PATH_RE.search(norm):
        return True
    # JUnit / Kotlin: FooTest.java, BarTests.kt
    return bool(re.search(r"Tests?\.(java|kt|scala)$", norm, re.IGNORECASE))


def _source_stem_variants(stem: str, suffix: str) -> List[str]:
    """Strip test naming conventions and return possible source base names."""
    variants: List[str] = []
    lower = stem.lower()

    if lower.startswith("test_") and len(stem) > 5:
        variants.append(stem[5:])
    if lower.endswith("_test") and len(stem) > 5:
        variants.append(stem[:-5])

    for marker in (".test", ".spec"):
        if lower.endswith(marker) and len(stem) > len(marker):
            variants.append(stem[: -len(marker)])

    # JUnit / Kotlin: UserTest.java, UserTest.kt
    if stem.endswith("Test") and suffix.lower() in (".java", ".kt", ".scala"):
        variants.append(stem[:-4])
    if stem.endswith("Tests") and suffix.lower() in (".java", ".kt"):
        variants.append(stem[:-5])

    # Deduplicate while preserving order
    seen: set[str] = set()
    out: List[str] = []
    for v in variants:
        key = v.lower()
        if v and key not in seen:
            seen.add(key)
            out.append(v)
    return out


def _search_directories(test_path: Path) -> List[Path]:
    """Directories where the companion source file may live."""
    dirs = [test_path.parent]
    parent_name = test_path.parent.name.lower()
    if parent_name == "__tests__":
        dirs.append(test_path.parent.parent)
    elif parent_name in ("tests", "test", "spec", "specs"):
        dirs.append(test_path.parent.parent)
        dirs.append(test_path.parent.parent / "src")
    return dirs


def companion_source_candidates(test_path: str) -> List[str]:
    """Return likely companion source paths for *test_path*, most specific first."""
    if not is_test_file_path(test_path):
        return []

    p = Path(test_path.replace("\\", "/"))
    suffix = p.suffix
    candidates: List[str] = []

    for stem_variant in _source_stem_variants(p.stem, suffix):
        for directory in _search_directories(p):
            candidates.append(str(directory / f"{stem_variant}{suffix}"))

    seen: set[str] = set()
    ordered: List[str] = []
    for c in candidates:
        norm = c.replace("\\", "/")
        if norm not in seen:
            seen.add(norm)
            ordered.append(norm)
    return ordered


def resolve_companion_source(
    test_path: str,
    registered_paths: Iterable[str],
) -> Optional[str]:
    """Match *test_path* to one registered source path, if any."""
    paths = [p for p in registered_paths if p]
    if not paths:
        return None

    by_lower = {p.replace("\\", "/").lower(): p for p in paths}
    for candidate in companion_source_candidates(test_path):
        hit = by_lower.get(candidate.lower())
        if hit:
            return hit

    # Same-directory fallback: e.g. doctype/it_asset/{it_asset.py, test_it_asset.py}
    test_p = Path(test_path.replace("\\", "/"))
    variants = {v.lower() for v in _source_stem_variants(test_p.stem, test_p.suffix)}
    for rp in paths:
        rp_p = Path(rp.replace("\\", "/"))
        if rp_p.parent == test_p.parent and rp_p.stem.lower() in variants:
            return rp
    return None


def companion_source_exists_on_disk(test_path: str, workspace) -> bool:
    """True if any candidate companion source file exists under *workspace*."""
    from pathlib import Path as P

    ws = P(workspace)
    for candidate in companion_source_candidates(test_path):
        if (ws / candidate).is_file():
            return True
    return False
