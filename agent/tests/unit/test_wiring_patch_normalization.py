"""
The jq wiring patch is the only structured channel; don't corrupt it.

When a ``<wiring_patch>`` applies, the contract gets a real module and one
coherent package layout. When it fails, the pipeline falls back to scraping
paths out of prose — which is where every package pathology lives (unioned
layouts, dropped root-level sources, symbols filed under the alphabetically
first package). So patch failures are upstream of all of it.

``_normalize_jq_patch_program`` ends with a blind repair::

    if not program.startswith("."):
        program = "." + program

intended for a bare ``module = "x"``. But models frequently emit the *shell
invocation* rather than the filter body::

    jq '
      .module = "expense_tracker" | ...
    '

and the repair turns ``jq '`` into ``.jq '`` — a guaranteed syntax error.
Observed live on job 1cec01ad::

    jq wiring patch failed: jq: error: syntax error, unexpected
    INVALID_CHARACTER, expecting end of file (Unix shell quoting issues?)
    at <top-level>, line 1:
    .jq '
    wiring jq patch invalid; strengthening path-only seed from prose

The model had reasoned correctly — "Let's define package expense_tracker.
Files: expense_tracker/main.py, expense_tracker/api.py, ..." — a single
coherent layout. The wrapper cost the job its structured contract, and the
prose fallback then locked a contract containing nothing but ``tests``.

Two rules: unwrap a shell invocation, and only prepend ``.`` when what follows
actually looks like a field assignment. Prepending it to arbitrary text
manufactures a broken program out of an unusable one, and a broken program
still costs a jq subprocess and a confusing log line.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from llamaindex_crew.utils.wiring_contract import (  # noqa: E402
    _normalize_jq_patch_program,
    apply_wiring_patch,
    resolve_jq_bin,
)

requires_jq = pytest.mark.skipif(resolve_jq_bin() is None, reason="jq not installed")


def _seed():
    return {
        "version": 1,
        "module": "unknown",
        "language": "unknown",
        "packages": {},
        "symbols": {},
        "deps": [],
    }


# ── the live failure ────────────────────────────────────────────────────────

def test_shell_invocation_is_unwrapped_not_dotted():
    """Job 1cec01ad: `jq '` became `.jq '`."""
    patch = """jq '
.module = "expense_tracker"
| .language = "python"
'"""
    program = _normalize_jq_patch_program(patch)

    assert not program.startswith(".jq"), f"still corrupted: {program!r}"
    assert program.startswith(".module")
    assert "expense_tracker" in program


@requires_jq
def test_the_live_patch_now_applies_end_to_end():
    patch = """jq '
.module = "expense_tracker"
| .language = "python"
| .packages["expense_tracker"].files = ["expense_tracker/main.py", "expense_tracker/api.py"]
| .packages["expense_tracker"].owns = ["ExpenseTrackerAPI"]
'"""
    result = apply_wiring_patch(_seed(), patch)

    assert result is not None, "the patch the model actually emitted must apply"
    assert result["module"] == "expense_tracker"
    assert "expense_tracker" in result["packages"]


def test_shell_invocation_with_flags_is_unwrapped():
    program = _normalize_jq_patch_program("""jq -c '.module = "demo"'""")
    assert program == '.module = "demo"'


def test_fenced_shell_invocation_is_unwrapped():
    patch = """```bash
jq '.module = "demo" | .language = "go"'
```"""
    program = _normalize_jq_patch_program(patch)

    assert program.startswith(".module")
    assert "jq" not in program.split("=")[0]


def test_double_quoted_shell_invocation_is_unwrapped():
    program = _normalize_jq_patch_program('jq ".module = \\"demo\\""')
    assert program.startswith(".module") or program == ""


# ── the repair it was actually written for still works ──────────────────────

def test_a_bare_field_assignment_still_gets_its_dot():
    assert _normalize_jq_patch_program('module = "demo"') == '.module = "demo"'


def test_a_bare_indexed_assignment_still_gets_its_dot():
    program = _normalize_jq_patch_program('packages["app"].files = ["app/main.py"]')
    assert program.startswith(".packages[")


def test_a_correct_program_is_left_alone():
    src = '.module = "demo" | .language = "python"'
    assert _normalize_jq_patch_program(src) == src


def test_a_fenced_jq_program_is_unfenced():
    program = _normalize_jq_patch_program('```jq\n.module = "demo"\n```')
    assert program == '.module = "demo"'


# ── refuse rather than manufacture a broken program ─────────────────────────

def test_prose_is_rejected_rather_than_dotted():
    """Prepending '.' to a sentence yields a program that can only fail."""
    program = _normalize_jq_patch_program(
        "Here is the wiring patch you asked for, hope it helps!"
    )
    assert program == "", f"expected a clean reject, got {program!r}"


def test_empty_input_yields_empty_program():
    assert _normalize_jq_patch_program("") == ""
    assert _normalize_jq_patch_program("   \n  ") == ""


@requires_jq
def test_unusable_patch_returns_none_without_running_jq():
    assert apply_wiring_patch(_seed(), "not a jq program at all") is None
