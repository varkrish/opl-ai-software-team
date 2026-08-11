"""
One definition of "don't look in here", instead of eleven.

Ten places in this codebase each carried their own set of directories to skip,
no two alike:

    file_tools        .git __pycache__ node_modules .pytest_cache htmlcov .tox venv .venv
    tldr_tools        .git __pycache__ node_modules .pytest_cache venv .venv
    meta_agent        .git __pycache__ node_modules .venv venv dist build
    manifest_repair   node_modules .venv venv .git
    sandbox_client    …fifteen entries including .idea and .vscode
    refinement_*      two more variants, one with .tldr
    document_indexer  prefix match on "node_modules", "target", "build"
    wiring_contract   substring match: "venv" in path

The last two are not merely inconsistent, they are wrong. A prefix match only
catches vendored code at the workspace root, and a substring match on ``venv``
also swallows a legitimate ``my-venv-tool/`` package. Both are fixed here by
matching whole path components.

Three concepts were tangled together in those sets, which is why a single flat
union would be the wrong answer:

  * VENDOR   — someone else's source (node_modules, .venv, site-packages)
  * BUILD    — output this project generates (dist, target, __pycache__)
  * TOOLING  — vcs and editor state (.git, .tldr, .idea)

A dependency scanner wants VENDOR. A file walker wants all three. Keeping them
separate lets each caller say what it actually means.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from llamaindex_crew.utils.vendor_paths import (  # noqa: E402
    BUILD_DIRS,
    SKIP_DIRS,
    TOOLING_DIRS,
    VENDOR_DIRS,
    is_skippable,
    is_vendored,
    prune_dirnames,
)


# ── the concepts stay distinct ──────────────────────────────────────────────

def test_the_three_sets_do_not_overlap():
    assert not (VENDOR_DIRS & BUILD_DIRS)
    assert not (VENDOR_DIRS & TOOLING_DIRS)
    assert not (BUILD_DIRS & TOOLING_DIRS)


def test_skip_is_the_union():
    assert SKIP_DIRS == VENDOR_DIRS | BUILD_DIRS | TOOLING_DIRS


def test_each_set_holds_what_its_name_says():
    assert {"node_modules", ".venv", "venv", "site-packages"} <= VENDOR_DIRS
    assert {"dist", "build", "target", "__pycache__"} <= BUILD_DIRS
    assert {".git", ".tldr"} <= TOOLING_DIRS
    # Build output is not somebody else's source, and vice versa.
    assert "dist" not in VENDOR_DIRS
    assert "node_modules" not in BUILD_DIRS


# ── component matching, not substring or prefix ─────────────────────────────

@pytest.mark.parametrize("path", [
    "node_modules/left-pad/index.js",
    "frontend/node_modules/react/index.js",
    ".venv/lib/python3.11/site-packages/requests/api.py",
    "backend/venv/bin/activate",
])
def test_vendored_paths_are_recognised_at_any_depth(path):
    """document_indexer matched only a prefix, so nested vendoring slipped past."""
    assert is_vendored(path)


@pytest.mark.parametrize("path", [
    "my-venv-tool/main.py",
    "app/venvironment.py",
    "src/build_helpers.py",
    "app/distances.py",
    "node_modules_helper/util.js",
])
def test_lookalike_names_are_not_vendored(path):
    """
    wiring_contract used `"venv" in path`, which also swallows my-venv-tool/.
    Whole components only.
    """
    assert not is_vendored(path), f"{path} is project source"


def test_project_source_is_never_vendored():
    for path in ("app/main.py", "backend/requirements.txt", "src/index.js", "main.py"):
        assert not is_vendored(path)


def test_build_output_is_skippable_but_not_vendored():
    """A dependency scanner wants somebody else's source, not our own output."""
    assert is_skippable("target/classes/App.class")
    assert not is_vendored("target/classes/App.class")


def test_tooling_dirs_are_skippable():
    assert is_skippable(".git/config")
    assert is_skippable("app/.tldr/index.json")


# ── shapes callers actually pass ────────────────────────────────────────────

def test_absolute_and_windows_paths_work():
    assert is_vendored("/srv/app/node_modules/x/index.js")
    assert is_vendored("frontend\\node_modules\\react\\index.js")


def test_path_objects_work():
    assert is_vendored(Path("node_modules") / "left-pad" / "index.js")
    assert not is_vendored(Path("app") / "main.py")


def test_empty_and_odd_input_is_not_vendored():
    for value in ("", ".", "/", None):
        assert not is_vendored(value)


def test_a_bare_directory_name_matches():
    assert is_vendored("node_modules")
    assert is_skippable("dist")


# ── os.walk pruning ─────────────────────────────────────────────────────────

def test_prune_dirnames_edits_in_place_for_os_walk():
    """os.walk only honours del/slice assignment on the list it handed you."""
    dirnames = ["app", "node_modules", "tests", ".git", "dist"]

    prune_dirnames(dirnames)

    assert dirnames == ["app", "tests"]


def test_prune_dirnames_keeps_everything_when_nothing_matches():
    dirnames = ["app", "tests", "docs"]
    prune_dirnames(dirnames)
    assert dirnames == ["app", "tests", "docs"]


def test_prune_dirnames_returns_the_same_list_object():
    dirnames = ["app", "node_modules"]
    assert prune_dirnames(dirnames) is dirnames
