"""
Declared dependencies must be found wherever the manifest lives, under the name
the code actually imports.

``PythonStrategy._load_third_party_names`` looks in exactly one place::

    for manifest in ("requirements.txt", "setup.py", "setup.cfg"):
        req_file = workspace / manifest

and generates only hyphen/underscore variants of each name. Two consequences,
both seen on live job 107b3d3e, whose manifest sat at ``backend/requirements.txt``::

    fastapi==0.110.0
    uvicorn[standard]==0.27.0
    memmachine-client==0.1.5
    neo4j==5.14.0
    python-dotenv==1.0.0
    sse-starlette==1.6.5

The validator reported::

    integration:         Broken import: 'dotenv' (module not found in workspace)
                         Broken import: 'memmachine_client' (module not found in workspace)
    dependency_manifest: missing dotenv, memmachine_client, neo4j

Every one of those is declared. The manifest was simply never read, because it
was one directory down — the layout the model chose for a backend/frontend
split, and the layout its own wiring contract declared.

The second bug survives fixing the first: ``python-dotenv`` provides the module
``dotenv``. Hyphen/underscore variants yield ``python_dotenv``, which is not
what any code imports.

This matters more than a cosmetic false positive. These issues feed the
remediation loop, so the loop spends its budget rewriting correct code to
satisfy a check that was wrong — job 107b3d3e ended with
``fix_loop_not_converging: 9 issue(s) remain and no round improved on the best
of 9``.

Bias is toward silence: failing to report a genuinely missing dependency costs
one build error with a clear message, while a false report costs the whole fix
budget and corrupts working code.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from llamaindex_crew.orchestrator.language_strategies import PythonStrategy  # noqa: E402

REQUIREMENTS = (
    "fastapi==0.110.0\n"
    "uvicorn[standard]==0.27.0\n"
    "memmachine-client==0.1.5\n"
    "neo4j==5.14.0\n"
    "python-dotenv==1.0.0\n"
    "sse-starlette==1.6.5\n"
)


def _names(workspace):
    return PythonStrategy()._load_third_party_names(Path(workspace))


# ── manifest discovery ──────────────────────────────────────────────────────

def test_requirements_in_a_service_subdirectory_are_found(tmp_path):
    """The live layout: backend/requirements.txt beside frontend/."""
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "requirements.txt").write_text(REQUIREMENTS, encoding="utf-8")

    names = _names(tmp_path)

    for pkg in ("neo4j", "fastapi", "sse_starlette"):
        assert pkg in names, f"{pkg} is declared but was not discovered"


def test_root_requirements_still_work(tmp_path):
    (tmp_path / "requirements.txt").write_text(REQUIREMENTS, encoding="utf-8")
    assert "neo4j" in _names(tmp_path)


def test_vendor_directories_are_not_scanned(tmp_path):
    """node_modules and virtualenvs carry thousands of unrelated manifests."""
    for vendor in ("node_modules", ".venv"):
        (tmp_path / vendor / "pkg").mkdir(parents=True)
        (tmp_path / vendor / "pkg" / "requirements.txt").write_text(
            "some-vendored-thing==1.0\n", encoding="utf-8"
        )

    assert "some_vendored_thing" not in _names(tmp_path)


def test_several_service_manifests_are_merged(tmp_path):
    for svc, pkg in (("api", "flask==3.0.0"), ("worker", "celery==5.3.0")):
        (tmp_path / svc).mkdir()
        (tmp_path / svc / "requirements.txt").write_text(pkg + "\n", encoding="utf-8")

    names = _names(tmp_path)

    assert "flask" in names and "celery" in names


# ── distribution name vs import name ────────────────────────────────────────

@pytest.mark.parametrize("distribution,imported", [
    ("python-dotenv", "dotenv"),
    ("python-jose", "jose"),
    ("python-multipart", "multipart"),
    ("msgpack-python", "msgpack"),
])
def test_the_python_prefix_convention_is_understood(tmp_path, distribution, imported):
    """`python-x` distributions import as `x` — the dominant PyPI convention."""
    (tmp_path / "requirements.txt").write_text(f"{distribution}==1.0.0\n", encoding="utf-8")

    assert imported in _names(tmp_path)


@pytest.mark.parametrize("distribution,imported", [
    ("pyyaml", "yaml"),
    ("pillow", "PIL"),
    ("beautifulsoup4", "bs4"),
    ("scikit-learn", "sklearn"),
    ("opencv-python", "cv2"),
    ("psycopg2-binary", "psycopg2"),
])
def test_irregular_distributions_map_to_their_real_module(tmp_path, distribution, imported):
    """These follow no rule; they are facts about specific packages, like the stdlib list."""
    (tmp_path / "requirements.txt").write_text(f"{distribution}==1.0.0\n", encoding="utf-8")

    assert imported in _names(tmp_path)


def test_the_distribution_name_itself_still_resolves(tmp_path):
    """Mapping must add names, never replace them."""
    (tmp_path / "requirements.txt").write_text("python-dotenv==1.0.0\n", encoding="utf-8")

    names = _names(tmp_path)

    assert "dotenv" in names
    assert "python_dotenv" in names and "python-dotenv" in names


def test_underscore_distributions_still_resolve(tmp_path):
    (tmp_path / "requirements.txt").write_text("memmachine-client==0.1.5\n", encoding="utf-8")
    assert "memmachine_client" in _names(tmp_path)


# ── end to end: the live false positives ────────────────────────────────────

def test_the_live_imports_are_no_longer_reported_broken(tmp_path):
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "requirements.txt").write_text(REQUIREMENTS, encoding="utf-8")
    src = tmp_path / "backend" / "main.py"
    src.write_text(
        "import os\n"
        "from dotenv import load_dotenv\n"
        "import memmachine_client\n"
        "from neo4j import GraphDatabase\n"
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n",
        encoding="utf-8",
    )

    result = PythonStrategy().validate_imports(src, tmp_path)

    assert result["broken_imports"] == [], (
        f"all of these are declared in backend/requirements.txt: "
        f"{result['broken_imports']}"
    )


def test_a_genuinely_undeclared_import_is_still_reported(tmp_path):
    """The check must keep working, or this fix would just hide real defects."""
    (tmp_path / "requirements.txt").write_text("fastapi==0.110.0\n", encoding="utf-8")
    src = tmp_path / "main.py"
    src.write_text("import nowhere_declared_at_all\n", encoding="utf-8")

    result = PythonStrategy().validate_imports(src, tmp_path)

    assert any(b["module"] == "nowhere_declared_at_all"
               for b in result["broken_imports"])


def test_no_manifest_at_all_does_not_crash(tmp_path):
    names = _names(tmp_path)
    assert isinstance(names, set)


# ── the same bug in every other strategy ────────────────────────────────────
#
# Root-only manifest lookup is not a Python problem. JavaScript reads
# ``workspace/package.json`` and Java reads ``workspace/pom.xml``, so the moment
# a project puts its services in subdirectories — which the wiring contract
# actively encourages, and which job 107b3d3e did with backend/ and frontend/ —
# every declared dependency in that ecosystem becomes invisible too.
#
# Discovery belongs to the base class: each strategy names its manifest files,
# and the traversal and vendor-exclusion are shared.

def test_javascript_finds_a_package_json_in_a_service_directory(tmp_path):
    from llamaindex_crew.orchestrator.language_strategies import JavaScriptStrategy

    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "package.json").write_text(
        '{"dependencies": {"react": "^18.2.0", "recharts": "^2.10.0"},'
        ' "devDependencies": {"vite": "^5.0.0"}}',
        encoding="utf-8",
    )

    names = JavaScriptStrategy().load_declared_dependencies(tmp_path)

    assert {"react", "recharts", "vite"} <= names


def test_javascript_still_reads_a_root_package_json(tmp_path):
    from llamaindex_crew.orchestrator.language_strategies import JavaScriptStrategy

    (tmp_path / "package.json").write_text(
        '{"dependencies": {"express": "^4.18.0"}}', encoding="utf-8"
    )
    assert "express" in JavaScriptStrategy().load_declared_dependencies(tmp_path)


def test_javascript_ignores_node_modules(tmp_path):
    from llamaindex_crew.orchestrator.language_strategies import JavaScriptStrategy

    (tmp_path / "node_modules" / "left-pad").mkdir(parents=True)
    (tmp_path / "node_modules" / "left-pad" / "package.json").write_text(
        '{"dependencies": {"vendored-transitive": "1.0.0"}}', encoding="utf-8"
    )

    assert "vendored-transitive" not in JavaScriptStrategy().load_declared_dependencies(tmp_path)


def test_java_finds_module_poms(tmp_path):
    """Multi-module Maven keeps its real dependencies in the module POMs."""
    from llamaindex_crew.orchestrator.language_strategies import JavaStrategy

    (tmp_path / "service").mkdir()
    (tmp_path / "service" / "pom.xml").write_text(
        "<project><dependencies><dependency>"
        "<groupId>org.springframework.boot</groupId>"
        "<artifactId>spring-boot-starter-web</artifactId>"
        "</dependency></dependencies></project>",
        encoding="utf-8",
    )

    deps = JavaStrategy().load_declared_dependencies(tmp_path)

    assert "org.springframework.boot:spring-boot-starter-web" in deps


def test_java_finds_a_gradle_file_in_a_subdirectory(tmp_path):
    from llamaindex_crew.orchestrator.language_strategies import JavaStrategy

    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "build.gradle").write_text(
        "dependencies { implementation 'com.google.guava:guava:32.1.2-jre' }",
        encoding="utf-8",
    )

    assert "com.google.guava:guava" in JavaStrategy().load_declared_dependencies(tmp_path)


def test_java_ignores_build_output(tmp_path):
    from llamaindex_crew.orchestrator.language_strategies import JavaStrategy

    (tmp_path / "build" / "tmp").mkdir(parents=True)
    (tmp_path / "build" / "tmp" / "pom.xml").write_text(
        "<project><dependencies><dependency>"
        "<groupId>stale</groupId><artifactId>artifact</artifactId>"
        "</dependency></dependencies></project>",
        encoding="utf-8",
    )

    assert "stale:artifact" not in JavaStrategy().load_declared_dependencies(tmp_path)


def test_every_strategy_survives_an_empty_workspace(tmp_path):
    from llamaindex_crew.orchestrator.language_strategies import (
        JavaScriptStrategy, JavaStrategy, PythonStrategy as PS,
    )

    for strategy in (PS(), JavaScriptStrategy(), JavaStrategy()):
        assert isinstance(strategy.load_declared_dependencies(tmp_path), set)
