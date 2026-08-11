"""
When the project declares how to run itself, read it instead of guessing.

Preview currently reasons from filenames — is there a main.py, is there a
package.json — which is how job 107b3d3e ended up with "No Python entrypoint
found" for a project whose entrypoint was one directory down. Meanwhile that
same job had already written a docker-compose.yml naming both services, their
ports and their commands. The answer was on disk and nothing read it.

``dev-compose.yaml`` is that declaration in a form preview can use: no image
builds, source mounted, install-and-run in the command, and an ``x-preview``
marker saying which service's port is the URL a human should open.

Verified, not trusted — same rule as ``preview_command``. This is the model
that pinned a nonexistent ``memmachine-client==0.1.5`` on this very job, so a
compose file that names a missing directory, omits its primary service, or
binds loopback is rejected in favour of the next layer down. A rejected file
costs one fallback; an accepted bad one costs a container that cannot serve and
a user staring at a dead URL.

Parsed without a YAML dependency being assumed present: the loader degrades to
returning nothing rather than raising, because preview must keep working.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent" / "src"))

yaml = pytest.importorskip("yaml", reason="PyYAML required to parse compose files")

from crew_studio.compose_preview import (  # noqa: E402
    ComposePreview,
    read_dev_compose_preview,
)

LIVE_COMPOSE = """\
version: "3.8"
x-preview:
  primary: frontend
services:
  collector:
    image: python:3.11-slim
    working_dir: /app
    volumes:
      - ./backend:/app
    command: sh -c "pip install -r requirements.txt && python3 main.py"
    ports:
      - "8000:8000"
  frontend:
    image: node:20-slim
    working_dir: /app
    volumes:
      - ./frontend:/app
    command: sh -c "npm install && npm run dev"
    ports:
      - "3000:3000"
"""


def _project(tmp_path, compose=LIVE_COMPOSE, dirs=("backend", "frontend")):
    for d in dirs:
        (tmp_path / d).mkdir(exist_ok=True)
    (tmp_path / "dev-compose.yaml").write_text(compose, encoding="utf-8")
    return tmp_path


# ── reading the declaration ─────────────────────────────────────────────────

def test_the_primary_service_decides_the_preview(tmp_path):
    preview = read_dev_compose_preview(_project(tmp_path))

    assert isinstance(preview, ComposePreview)
    assert preview.service == "frontend"
    assert preview.port == 3000
    assert "npm run dev" in preview.command


def test_the_command_runs_from_the_mounted_directory(tmp_path):
    """`npm install` in the wrong directory installs the wrong package.json."""
    preview = read_dev_compose_preview(_project(tmp_path))

    assert preview.workdir == "frontend"


def test_all_services_are_reported_for_the_caller_to_show(tmp_path):
    preview = read_dev_compose_preview(_project(tmp_path))

    assert sorted(preview.services) == ["collector", "frontend"]


def test_a_yaml_extension_is_also_accepted(tmp_path):
    _project(tmp_path)
    (tmp_path / "dev-compose.yaml").rename(tmp_path / "dev-compose.yml")

    assert read_dev_compose_preview(tmp_path) is not None


def test_a_single_service_needs_no_primary_marker(tmp_path):
    compose = """\
services:
  api:
    image: python:3.11-slim
    working_dir: /app
    volumes: ["./backend:/app"]
    command: sh -c "python3 main.py"
    ports: ["8000:8000"]
"""
    preview = read_dev_compose_preview(_project(tmp_path, compose, dirs=("backend",)))

    assert preview is not None and preview.service == "api"


# ── rejected rather than trusted ────────────────────────────────────────────

def test_a_mount_of_a_missing_directory_is_rejected(tmp_path):
    """The same model invented a version that did not exist on this job."""
    compose = LIVE_COMPOSE.replace("./frontend:/app", "./web-ui:/app")

    assert read_dev_compose_preview(_project(tmp_path, compose)) is None


def test_a_missing_primary_service_is_rejected(tmp_path):
    compose = LIVE_COMPOSE.replace("primary: frontend", "primary: web")

    assert read_dev_compose_preview(_project(tmp_path, compose)) is None


def test_multiple_services_without_a_marker_are_rejected(tmp_path):
    """Opening the wrong service is worse than falling back."""
    compose = LIVE_COMPOSE.replace("x-preview:\n  primary: frontend\n", "")

    assert read_dev_compose_preview(_project(tmp_path, compose)) is None


def test_a_loopback_bind_is_rejected(tmp_path):
    """127.0.0.1 inside a container is unreachable from outside it."""
    compose = LIVE_COMPOSE.replace(
        'command: sh -c "npm install && npm run dev"',
        'command: sh -c "npm install && npm run dev -- --host 127.0.0.1"',
    )

    assert read_dev_compose_preview(_project(tmp_path, compose)) is None


def test_a_build_section_is_rejected(tmp_path):
    """Preview must not pay an image build; that is the production file's job."""
    compose = LIVE_COMPOSE.replace(
        "    image: node:20-slim\n",
        "    build:\n      context: ./frontend\n",
    )

    assert read_dev_compose_preview(_project(tmp_path, compose)) is None


def test_a_primary_service_with_no_published_port_is_rejected(tmp_path):
    compose = LIVE_COMPOSE.replace('      - "3000:3000"\n', "")

    assert read_dev_compose_preview(_project(tmp_path, compose)) is None


def test_a_primary_service_with_no_command_is_rejected(tmp_path):
    compose = LIVE_COMPOSE.replace(
        '    command: sh -c "npm install && npm run dev"\n', ""
    )

    assert read_dev_compose_preview(_project(tmp_path, compose)) is None


# ── never break preview ─────────────────────────────────────────────────────

def test_no_compose_file_is_not_an_error(tmp_path):
    assert read_dev_compose_preview(tmp_path) is None


def test_malformed_yaml_does_not_raise(tmp_path):
    (tmp_path / "dev-compose.yaml").write_text("services: [unclosed\n", encoding="utf-8")
    assert read_dev_compose_preview(tmp_path) is None


def test_an_empty_file_does_not_raise(tmp_path):
    (tmp_path / "dev-compose.yaml").write_text("", encoding="utf-8")
    assert read_dev_compose_preview(tmp_path) is None


def test_a_non_mapping_document_does_not_raise(tmp_path):
    (tmp_path / "dev-compose.yaml").write_text("- just\n- a list\n", encoding="utf-8")
    assert read_dev_compose_preview(tmp_path) is None


# ── wired into preview, below the explicit command ──────────────────────────

def test_detect_preview_uses_the_declared_compose(tmp_path):
    from crew_studio.preview_runner import detect_preview

    _project(tmp_path)
    (tmp_path / "frontend" / "package.json").write_text(
        '{"scripts": {"dev": "vite"}}', encoding="utf-8"
    )

    command, port = detect_preview(tmp_path, "node")

    assert "npm run dev" in command
    assert "cd frontend" in command
    assert port == 3000


def test_an_explicit_preview_command_still_outranks_compose(tmp_path):
    from crew_studio.preview_runner import detect_preview

    _project(tmp_path)
    (tmp_path / "test_plan.md").write_text(
        "preview_command: python3 backend/main.py\n", encoding="utf-8"
    )
    (tmp_path / "backend" / "main.py").write_text("print('x')\n", encoding="utf-8")

    command, _port = detect_preview(tmp_path, "python")

    assert command == "python3 backend/main.py"


def test_a_rejected_compose_falls_through_to_detection(tmp_path):
    from crew_studio.preview_runner import detect_preview

    _project(tmp_path, LIVE_COMPOSE.replace("primary: frontend", "primary: nope"))
    (tmp_path / "backend" / "main.py").write_text(
        "if __name__ == '__main__':\n    pass\n", encoding="utf-8"
    )

    command, _port = detect_preview(tmp_path, "python")

    assert "backend/main.py" in command
