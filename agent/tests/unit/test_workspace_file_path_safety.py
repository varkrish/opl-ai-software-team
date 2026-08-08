"""The workspace file-content route must not read outside a job workspace.

``/api/workspace/files/<path:file_path>`` builds ``job_workspace / file_path``
directly. Flask's ``<path:>`` converter passes ``..`` segments through
untouched, so without an explicit guard the route is an arbitrary file read for
anyone who can reach the port — and it is the same route the UI uses to show
build logs.
"""
import ast
import textwrap
from pathlib import Path

import pytest

_WEB_APP = (
    Path(__file__).parent.parent.parent.parent / "crew_studio" / "llamaindex_web_app.py"
)


def _function_source(name: str) -> str:
    """Return one function's source, without importing the whole Flask app."""
    source = _WEB_APP.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return textwrap.dedent(ast.get_source_segment(source, node))
    raise AssertionError(f"{name} not found in {_WEB_APP}")


def _load_guard():
    namespace: dict = {}
    exec(_function_source("_is_safe_relative_path"), namespace)
    return namespace["_is_safe_relative_path"]


is_safe = _load_guard()


@pytest.mark.parametrize("path", [
    "../../../../etc/passwd",
    "..%2f..%2fetc/passwd".replace("%2f", "/"),
    "subdir/../../../../etc/shadow",
    "/etc/passwd",
    "/absolute/path",
    "..",
    "a/../../b",
    "with\x00null",
    "",
])
def test_rejects_escapes(path):
    assert is_safe(path) is False


@pytest.mark.parametrize("path", [
    "main.py",
    "src/app/server.js",
    "smoke_test_container.log",
    "validation_report.json",
    "deep/nested/dir/file.txt",
    "file.with.dots.py",
])
def test_allows_ordinary_workspace_paths(path):
    assert is_safe(path) is True


def test_route_calls_the_guard():
    """The guard exists but was previously never called from this route."""
    body = _function_source("get_file_content")
    assert "_is_safe_relative_path(file_path)" in body, (
        "get_file_content must validate file_path before building a filesystem path"
    )


def test_route_contains_resolved_path_within_root():
    """A symlink inside the workspace can still escape it; string checks miss that."""
    body = _function_source("get_file_content")
    assert ".resolve().relative_to(" in body
