"""Generated code must not shadow risky Python builtins at module level.

Observed live 2026-08-08: a generated todo.py contained

    def list() -> List[Dict]:  # noqa: A001 - name matches required signature

Every static check passed — the file parses, imports resolve, structure is valid
— and the job was marked ``completed``. It crashed on first run because
``isinstance(data, list)`` then resolved ``list`` to the function, not the type.

Note the ``# noqa``: the model recognised the violation and suppressed the
linter. The check is therefore an AST walk, which a comment cannot silence.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator as V


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return tmp_path


def test_detects_the_live_failure(tmp_path):
    """The exact shape that shipped broken code, noqa included."""
    _write(tmp_path, "todo.py", (
        "from typing import List, Dict\n\n"
        "def list() -> List[Dict]:  # noqa: A001 - name matches required signature\n"
        "    return []\n"
    ))
    result = V.validate_builtin_shadowing(tmp_path)
    assert result["valid"] is False
    assert result["violations"][0]["name"] == "list"
    assert result["violations"][0]["file"] == "todo.py"
    assert result["violations"][0]["line"] == 3


@pytest.mark.parametrize("decl", [
    "def dict():\n    pass\n",
    "def str():\n    pass\n",
    "class set:\n    pass\n",
    "type = 'something'\n",
    "async def bytes():\n    pass\n",
])
def test_detects_various_shadow_forms(tmp_path, decl):
    _write(tmp_path, "m.py", decl)
    assert V.validate_builtin_shadowing(tmp_path)["valid"] is False


def test_clean_project_passes(tmp_path):
    _write(tmp_path, "todo.py", (
        "from typing import List, Dict\n\n"
        "def list_tasks() -> List[Dict]:\n"
        "    return []\n\n"
        "def add(task: str) -> None:\n"
        "    pass\n"
    ))
    assert V.validate_builtin_shadowing(tmp_path)["valid"] is True


def test_local_variable_named_list_is_allowed(tmp_path):
    """Function-local rebinding is scoped and does not break module-level type use."""
    _write(tmp_path, "m.py", (
        "def process(data):\n"
        "    list = [1, 2, 3]\n"
        "    return list\n"
    ))
    assert V.validate_builtin_shadowing(tmp_path)["valid"] is True


def test_common_verbs_are_not_flagged(tmp_path):
    """print/open/input are ordinary handler names in generated CLIs — no noise."""
    _write(tmp_path, "cli.py", (
        "def print():\n    pass\n\n"
        "def open():\n    pass\n\n"
        "def input():\n    pass\n"
    ))
    assert V.validate_builtin_shadowing(tmp_path)["valid"] is True


def test_skips_vendor_and_cache_dirs(tmp_path):
    _write(tmp_path, "node_modules/pkg/m.py", "def list():\n    pass\n")
    _write(tmp_path, ".venv/lib/m.py", "def dict():\n    pass\n")
    assert V.validate_builtin_shadowing(tmp_path)["valid"] is True


def test_syntax_error_is_not_this_checks_job(tmp_path):
    _write(tmp_path, "broken.py", "def list(:\n")
    assert V.validate_builtin_shadowing(tmp_path)["valid"] is True
