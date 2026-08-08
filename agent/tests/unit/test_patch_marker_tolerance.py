"""SEARCH/REPLACE markers must tolerate the bracket counts small models emit.

Observed live 2026-08-08: a refinement took ~5 minutes because the model produced
semantically perfect diffs three times and had two rejected purely on punctuation
— it wrote `<<<<<< SEARCH` (6 brackets) while the parser accepted only exactly 7
or exactly 4. Each rejection costs a full agent round-trip, and weaker models may
never converge on the exact count at all.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from llamaindex_crew.tools.file_tools import patch_file_content, set_allowed_file_paths

BASE = "def divide(a, b):\n    return a / b\n"


@pytest.fixture
def workspace(tmp_path):
    set_allowed_file_paths(None, workspace=str(tmp_path))
    (tmp_path / "c.py").write_text(BASE, encoding="utf-8")
    return tmp_path


def _blocks(open_m: str, div: str, close_m: str) -> str:
    return (
        f"{open_m} SEARCH\n"
        "def divide(a, b):\n"
        f"{div}\n"
        "def power(a, b):\n"
        f"{close_m} REPLACE\n"
    )


@pytest.mark.parametrize(
    "open_m,div,close_m",
    [
        ("<<<<<<<", "=======", ">>>>>>>"),   # canonical 7
        ("<<<<", "====", ">>>>"),            # short 4
        ("<<<<<<", "======", ">>>>>>"),      # 6 — the live failure
        ("<<<<<", "=========", ">>>>>"),     # mixed widths
        ("<<<", "===", ">>>"),               # minimum 3
    ],
    ids=["7-canonical", "4-short", "6-live-failure", "mixed", "3-minimum"],
)
def test_marker_widths_accepted(workspace, open_m, div, close_m):
    result = patch_file_content(
        file_path="c.py",
        diff_blocks=_blocks(open_m, div, close_m),
        workspace_path=str(workspace),
    )
    assert result.startswith("✅"), result
    assert "power" in (workspace / "c.py").read_text(encoding="utf-8")


def test_empty_replace_deletes(workspace):
    result = patch_file_content(
        file_path="c.py",
        diff_blocks="<<<<<< SEARCH\n    return a / b\n======\n>>>>>> REPLACE\n",
        workspace_path=str(workspace),
    )
    assert result.startswith("✅"), result
    assert "return a / b" not in (workspace / "c.py").read_text(encoding="utf-8")


def test_divider_inside_replacement_is_content(tmp_path):
    """A '====' line in REPLACE content must survive as content, not split the block."""
    set_allowed_file_paths(None, workspace=str(tmp_path))
    doc = tmp_path / "README.md"
    doc.write_text("Title\nplaceholder\n", encoding="utf-8")

    result = patch_file_content(
        file_path="README.md",
        diff_blocks=(
            "<<<<<<< SEARCH\nplaceholder\n=======\nHeading\n=======\nbody text\n>>>>>>> REPLACE\n"
        ),
        workspace_path=str(tmp_path),
    )
    assert result.startswith("✅"), result
    text = doc.read_text(encoding="utf-8")
    assert "Heading" in text and "body text" in text


def test_garbage_still_rejected(workspace):
    result = patch_file_content(
        file_path="c.py",
        diff_blocks="please change divide into power",
        workspace_path=str(workspace),
    )
    assert result.startswith("❌"), result
    assert (workspace / "c.py").read_text(encoding="utf-8") == BASE
