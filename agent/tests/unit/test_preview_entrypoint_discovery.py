"""
Start Preview must find the entrypoint where the project actually put it.

``preview_runner._python_entrypoint`` looks for ``main.py``/``app.py``/… in the
workspace root and, failing that, globs ``workspace/*.py`` for a ``__main__``
guard. Both are root-only.

Live, job 107b3d3e. Start Preview returned::

    {"error": "No Python entrypoint found (looked for main.py, app.py, server.py, run.py)"}

The project has one — ``backend/main.py``, ending in::

    if __name__ == "__main__":
        uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))

It imports nothing relative, so running the file directly works, and it binds
the same port ``DEFAULT_PORT`` already uses. Nothing about the project was
wrong; the runner only looked in one directory.

The inconsistency is visible inside a single validation report for that job:
``entrypoint`` passed, because ``PythonStrategy.validate_entrypoint`` walks the
tree with ``rglob``. The same workspace is "valid FastAPI entrypoint" to the
validator and "no entrypoint found" to the preview runner.

This is the third place the same root-only assumption has bitten — after the
wiring seed dropping flat-layout sources and every strategy missing
``backend/requirements.txt``. A backend/frontend split is the layout the model
chooses and its own wiring contract declares.

Dependency install has the identical flaw: ``detect_preview`` only prepends a
``pip install`` when ``workspace/requirements.txt`` exists, so a discovered
subdirectory entrypoint would start without its dependencies.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent" / "src"))

from crew_studio.preview_runner import (  # noqa: E402
    DEFAULT_PORT,
    PreviewError,
    _python_entrypoint,
    detect_preview,
)

MAIN_PY = (
    "import os\n"
    "from fastapi import FastAPI\n"
    "app = FastAPI()\n"
    "if __name__ == '__main__':\n"
    "    import uvicorn\n"
    "    uvicorn.run(app, host='0.0.0.0', port=int(os.getenv('PORT', '8000')))\n"
)


def _live_layout(tmp_path):
    """The shape of job 107b3d3e."""
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "main.py").write_text(MAIN_PY, encoding="utf-8")
    (tmp_path / "backend" / "__init__.py").write_text("from .main import app\n", encoding="utf-8")
    (tmp_path / "backend" / "requirements.txt").write_text("fastapi==0.110.0\n", encoding="utf-8")
    (tmp_path / "frontend" / "src").mkdir(parents=True)
    (tmp_path / "frontend" / "package.json").write_text('{"name":"ui"}', encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "backend_test.py").write_text("def test_x(): pass\n", encoding="utf-8")
    return tmp_path


# ── the live failure ────────────────────────────────────────────────────────

def test_entrypoint_in_a_service_directory_is_found(tmp_path):
    assert _python_entrypoint(_live_layout(tmp_path)) == "backend/main.py"


def test_detect_preview_produces_a_runnable_command(tmp_path):
    command, port = detect_preview(_live_layout(tmp_path), "python")

    assert "backend/main.py" in command
    assert port == DEFAULT_PORT


def test_the_subdirectory_requirements_are_installed(tmp_path):
    """A discovered entrypoint that starts without its dependencies just crashes."""
    command, _ = detect_preview(_live_layout(tmp_path), "python")

    assert "backend/requirements.txt" in command, (
        f"deps must be installed from where they live: {command}"
    )
    assert command.index("pip install") < command.index("python3"), (
        "install has to run before the app starts"
    )


# ── the root case must not regress ──────────────────────────────────────────

def test_a_root_entrypoint_still_wins(tmp_path):
    (tmp_path / "main.py").write_text(MAIN_PY, encoding="utf-8")
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "app.py").write_text(MAIN_PY, encoding="utf-8")

    assert _python_entrypoint(tmp_path) == "main.py"


def test_root_requirements_still_used(tmp_path):
    (tmp_path / "main.py").write_text(MAIN_PY, encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("fastapi\n", encoding="utf-8")

    command, _ = detect_preview(tmp_path, "python")

    assert "requirements.txt" in command and "python3 main.py" in command


def test_a_declared_preview_command_still_wins(tmp_path):
    _live_layout(tmp_path)
    (tmp_path / "test_plan.md").write_text(
        "preview_command: uvicorn backend.main:app --port 9001\n", encoding="utf-8"
    )

    command, port = detect_preview(tmp_path, "python")

    assert command == "uvicorn backend.main:app --port 9001"
    assert port == 9001


# ── the model declares, we verify ───────────────────────────────────────────
#
# Asking the model how to start its own app is the right primary mechanism: it
# has the whole design in context and writes the line at build time, alongside
# the test commands it already emits. But it is the same model that pinned a
# nonexistent memmachine-client==0.1.5 on this very job, so the declaration is
# preferred, not trusted. A bad command cannot be checked at preview time
# except by running it and waiting for a timeout — so it is checked here.

def test_a_command_naming_a_file_that_does_not_exist_is_ignored(tmp_path):
    _live_layout(tmp_path)
    (tmp_path / "test_plan.md").write_text(
        "preview_command: python3 src/server/app.py\n", encoding="utf-8"
    )

    command, _port = detect_preview(tmp_path, "python")

    assert "backend/main.py" in command, (
        f"a command naming a missing file must fall back to detection: {command}"
    )


def test_a_placeholder_command_is_ignored(tmp_path):
    _live_layout(tmp_path)
    (tmp_path / "test_plan.md").write_text(
        "preview_command: python3 <entrypoint>\n", encoding="utf-8"
    )

    command, _port = detect_preview(tmp_path, "python")

    assert "backend/main.py" in command


def test_an_explicit_none_falls_back_rather_than_running_none(tmp_path):
    _live_layout(tmp_path)
    (tmp_path / "test_plan.md").write_text("preview_command: none\n", encoding="utf-8")

    command, _port = detect_preview(tmp_path, "python")

    assert "backend/main.py" in command


def test_a_valid_declared_command_is_honoured_even_with_paths(tmp_path):
    """Verification must not reject correct commands."""
    _live_layout(tmp_path)
    (tmp_path / "test_plan.md").write_text(
        "preview_command: pip install -r backend/requirements.txt && "
        "python3 backend/main.py\n",
        encoding="utf-8",
    )

    command, port = detect_preview(tmp_path, "python")

    assert command.startswith("pip install -r backend/requirements.txt")
    assert port == DEFAULT_PORT


# ── discovery must stay unambiguous ─────────────────────────────────────────

def test_test_directories_are_not_treated_as_entrypoints(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "main.py").write_text(MAIN_PY, encoding="utf-8")

    with pytest.raises(PreviewError):
        _python_entrypoint_or_raise(tmp_path)


def test_vendor_directories_are_skipped(tmp_path):
    (tmp_path / ".venv" / "lib").mkdir(parents=True)
    (tmp_path / ".venv" / "lib" / "main.py").write_text(MAIN_PY, encoding="utf-8")

    with pytest.raises(PreviewError):
        _python_entrypoint_or_raise(tmp_path)


def test_two_competing_service_entrypoints_are_not_guessed_between(tmp_path):
    """Starting the wrong half of a project is worse than saying so."""
    for svc in ("service_a", "service_b"):
        (tmp_path / svc).mkdir()
        (tmp_path / svc / "main.py").write_text(MAIN_PY, encoding="utf-8")

    with pytest.raises(PreviewError) as excinfo:
        detect_preview(tmp_path, "python")

    message = str(excinfo.value)
    assert "service_a" in message and "service_b" in message, (
        f"the message must name the candidates so the user can pick: {message}"
    )


def test_an_empty_project_still_reports_clearly(tmp_path):
    with pytest.raises(PreviewError) as excinfo:
        detect_preview(tmp_path, "python")
    assert "entrypoint" in str(excinfo.value).lower()


def _python_entrypoint_or_raise(workspace):
    entry = _python_entrypoint(workspace)
    if not entry:
        raise PreviewError("no entrypoint")
    return entry


# ── the same bug in the node branch ─────────────────────────────────────────

def test_node_scripts_are_read_from_a_service_package_json(tmp_path):
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "package.json").write_text(
        '{"name": "ui", "scripts": {"start": "vite preview --port 8000"}}',
        encoding="utf-8",
    )

    command, _port = detect_preview(tmp_path, "node")

    assert "npm" in command, f"a start script exists but was not found: {command}"


def test_node_without_any_start_script_still_reports_clearly(tmp_path):
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "package.json").write_text('{"name": "ui"}', encoding="utf-8")

    with pytest.raises(PreviewError):
        detect_preview(tmp_path, "node")
