"""
The UI calls five endpoints. The server defines none. Fifteen checks pass.

Job 107b3d3e shipped an observability dashboard whose frontend fetches
``/api/v1/overview``, ``/api/v1/jobs``, ``/api/v1/memory``,
``/api/v1/infrastructure`` and ``/api/v1/stream``. Its backend/main.py is 386
lines with thirty references to sqlite3, neo4j, memmachine and httpx — and
exactly one decorator, ``@app.on_event("startup")``. There are no routes at all.

Validation said::

    completeness          pass
    entrypoint            pass   (framework: fastapi, missing_wiring: [])
    wiring_reconciliation pass

Every check asks whether the code is *arranged* correctly — files in the right
packages, imports resolving, an entrypoint wired. None asks whether the thing
the project exists to do exists. A project can be perfectly packaged and
functionally hollow.

The client is the specification here, and it is machine-readable: the frontend
literally names the URLs it depends on. Comparing them to the routes the server
defines is static analysis over both sides — no api_contract.yaml needed, no
model, and the same route patterns the strategies already carry.

Biased hard toward false negatives, because a false "missing endpoint" sends the
fix loop to invent routes that should not exist:

  * only same-origin calls count — an external API is not this server's job
  * suffix matching, so a router mounted under a prefix still satisfies a call
  * path parameters normalised on both sides
  * silent unless a server framework is actually present
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator  # noqa: E402


def _check(tmp_path):
    return CodeCompletenessValidator.validate_client_server_contract(tmp_path)


def _frontend(tmp_path, body):
    (tmp_path / "frontend" / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "frontend" / "src" / "index.js").write_text(body, encoding="utf-8")


def _backend(tmp_path, body):
    (tmp_path / "backend").mkdir(parents=True, exist_ok=True)
    (tmp_path / "backend" / "main.py").write_text(body, encoding="utf-8")


# ── the live failure ────────────────────────────────────────────────────────

LIVE_FRONTEND = """
const BASE = '/api/v1';
async function loadOverview() { return (await fetch('/api/v1/overview')).json(); }
async function loadJobs()     { return (await fetch('/api/v1/jobs')).json(); }
async function loadMemory()   { return (await fetch('/api/v1/memory')).json(); }
async function loadInfra()    { return (await fetch('/api/v1/infrastructure')).json(); }
const events = new EventSource('/api/v1/stream');
"""

LIVE_BACKEND = """
import os, sqlite3, httpx
from fastapi import FastAPI

app = FastAPI(title="OPL Crew Observability Collector")

@app.on_event("startup")
async def startup():
    pass

def collect_jobs():
    conn = sqlite3.connect(os.getenv("JOB_DB_PATH"))
    return conn.execute("select 1").fetchall()
"""


def test_a_backend_with_no_routes_is_reported(tmp_path):
    _frontend(tmp_path, LIVE_FRONTEND)
    _backend(tmp_path, LIVE_BACKEND)

    result = _check(tmp_path)

    assert not result["valid"], "a UI calling five absent endpoints must not pass"
    missing = {c["url"] for c in result["unreachable_calls"]}
    assert missing == {
        "/api/v1/overview", "/api/v1/jobs", "/api/v1/memory",
        "/api/v1/infrastructure", "/api/v1/stream",
    }


def test_the_report_names_the_calling_file(tmp_path):
    """Without the file, the fix loop has nowhere to start."""
    _frontend(tmp_path, LIVE_FRONTEND)
    _backend(tmp_path, LIVE_BACKEND)

    result = _check(tmp_path)

    assert all(c["files"] for c in result["unreachable_calls"])
    assert any("frontend/src/index.js" in f
               for c in result["unreachable_calls"] for f in c["files"])


def test_implemented_endpoints_are_not_reported(tmp_path):
    _frontend(tmp_path, "fetch('/api/v1/jobs');\n")
    _backend(tmp_path, """
from fastapi import FastAPI
app = FastAPI()

@app.get("/api/v1/jobs")
async def jobs():
    return []
""")

    assert _check(tmp_path)["valid"]


# ── false positives would send the loop inventing routes ────────────────────

def test_a_router_mounted_under_a_prefix_still_satisfies_the_call(tmp_path):
    """`APIRouter(prefix=...)` means the decorator never holds the full path."""
    _frontend(tmp_path, "fetch('/api/v1/overview');\n")
    _backend(tmp_path, """
from fastapi import APIRouter, FastAPI
router = APIRouter(prefix="/api/v1")

@router.get("/overview")
async def overview():
    return {}

app = FastAPI()
app.include_router(router)
""")

    result = _check(tmp_path)

    assert result["valid"], f"prefix-mounted route wrongly reported: {result}"


def test_path_parameters_match_across_the_two_spellings(tmp_path):
    """Client interpolates `${id}`; the server declares `{job_id}`."""
    _frontend(tmp_path, "fetch(`/api/v1/jobs/${id}`);\n")
    _backend(tmp_path, """
from fastapi import FastAPI
app = FastAPI()

@app.get("/api/v1/jobs/{job_id}")
async def one(job_id: str):
    return {}
""")

    assert _check(tmp_path)["valid"]


def test_external_urls_are_not_this_servers_responsibility(tmp_path):
    _frontend(tmp_path, """
fetch('https://api.github.com/repos/x/y');
fetch('http://localhost:9200/_search');
""")
    _backend(tmp_path, """
from fastapi import FastAPI
app = FastAPI()

@app.get("/health")
async def health():
    return {}
""")

    assert _check(tmp_path)["valid"]


def test_a_frontend_only_project_is_not_judged(tmp_path):
    """No server here, so every call is someone else's API."""
    _frontend(tmp_path, "fetch('/api/v1/anything');\n")

    result = _check(tmp_path)

    assert result["valid"]
    assert result.get("skipped")


def test_a_backend_only_project_is_not_judged(tmp_path):
    _backend(tmp_path, """
from fastapi import FastAPI
app = FastAPI()

@app.get("/api/v1/jobs")
async def jobs():
    return []
""")

    assert _check(tmp_path)["valid"]


def test_trailing_slashes_do_not_create_phantom_gaps(tmp_path):
    _frontend(tmp_path, "fetch('/api/v1/jobs/');\n")
    _backend(tmp_path, """
from fastapi import FastAPI
app = FastAPI()

@app.get("/api/v1/jobs")
async def jobs():
    return []
""")

    assert _check(tmp_path)["valid"]


def test_a_templated_base_alone_is_skipped_rather_than_guessed(tmp_path):
    """
    `${BACKEND_URL}/jobs` on its own could address any third-party API, so
    without evidence that this file talks to our server it is not judged.
    """
    _frontend(tmp_path, "fetch(`${BACKEND_URL}/jobs`);\n")
    _backend(tmp_path, """
from fastapi import FastAPI
app = FastAPI()

@app.get("/health")
async def health():
    return {}
""")

    assert _check(tmp_path)["valid"]


def test_templated_calls_count_once_the_file_proves_it_calls_our_server(tmp_path):
    """
    Job 107b3d3e's frontend builds four of its five calls as `${BASE}/x` and
    only the EventSource is a plain path. Skipping templates outright saw one
    call in five. The plain path is the evidence: this file addresses our own
    server, so its template tails name our endpoints too.
    """
    _frontend(tmp_path, """
const BASE = '/api/v1';
const events = new EventSource('/api/v1/stream');
async function overview() { return (await fetch(`${BASE}/overview`)).json(); }
async function infra()    { return (await fetch(`${BASE}/infrastructure`)).json(); }
""")
    _backend(tmp_path, """
from fastapi import APIRouter, FastAPI
api_router = APIRouter(prefix="/api/v1")

@api_router.get("/overview")
async def overview():
    return {}

@api_router.get("/infra")
async def infra():
    return {}

app = FastAPI()
app.include_router(api_router)
""")

    result = _check(tmp_path)
    missing = {c["url"] for c in result["unreachable_calls"]}

    assert "/overview" not in missing, "the implemented endpoint must not be flagged"
    assert "/infrastructure" in missing, "client says /infrastructure, server says /infra"
    assert "/api/v1/stream" in missing, "client says /stream, server says nothing"


def test_a_custom_fetch_hook_still_counts_as_a_call(tmp_path):
    """
    Job 107b3d3e's frontend calls nothing directly — every request goes through
    its own useFetch hook, so a fetch-only pattern saw one call in five.
    """
    _frontend(tmp_path, """
const { data } = useFetch('/api/v1/overview');
const jobs     = useFetch(`/api/v1/jobs?${query.toString()}`);
const infra    = useFetch('/api/v1/infrastructure');
""")
    _backend(tmp_path, """
from fastapi import APIRouter, FastAPI
api_router = APIRouter(prefix="/api/v1")

@api_router.get("/overview")
async def overview():
    return {}

@api_router.get("/jobs")
async def jobs():
    return []

@api_router.get("/infra")
async def infra():
    return {}

app = FastAPI()
app.include_router(api_router)
""")

    missing = {c["url"] for c in _check(tmp_path)["unreachable_calls"]}

    assert missing == {"/api/v1/infrastructure"}, (
        f"overview and jobs exist; only /infrastructure vs /infra differs: {missing}"
    )


def test_client_side_navigation_is_not_a_server_call(tmp_path):
    """navigate('/dashboard') is a router path, not an endpoint."""
    _frontend(tmp_path, """
navigate('/dashboard');
history.push('/settings');
router.push('/jobs/42');
""")
    _backend(tmp_path, """
from fastapi import FastAPI
app = FastAPI()

@app.get("/api/v1/health")
async def health():
    return {}
""")

    assert _check(tmp_path)["valid"]


def test_a_clients_own_axios_calls_are_not_counted_as_routes(tmp_path):
    """
    Found by running this against job 107b3d3e's real code, not by the
    synthetic cases above: the Express route pattern (`\\w+.get("/...")`) also
    matches a browser client's `axios.get("/api/v1/jobs")`. The frontend then
    answers its own calls and the check reports nothing. It claimed "7 routes
    found" against a backend that defines none.
    """
    _frontend(tmp_path, """
import axios from 'axios';
export const getJobs     = () => axios.get('/api/v1/jobs');
export const getOverview = () => axios.get('/api/v1/overview');
""")
    _backend(tmp_path, LIVE_BACKEND)

    result = _check(tmp_path)

    assert result["routes"] == 0, (
        f"a frontend declares no routes; got {result['routes']}"
    )
    assert {c["url"] for c in result["unreachable_calls"]} == {
        "/api/v1/jobs", "/api/v1/overview",
    }


def test_test_files_are_not_treated_as_clients(tmp_path):
    """A test mocking fetch is not a dependency on a real endpoint."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "frontend_test.js").write_text(
        "global.fetch = jest.fn();\nfetch('/api/v1/imaginary');\n", encoding="utf-8"
    )
    _backend(tmp_path, """
from fastapi import FastAPI
app = FastAPI()

@app.get("/health")
async def health():
    return {}
""")

    assert _check(tmp_path)["valid"]


# ── other stacks ────────────────────────────────────────────────────────────

def test_express_routes_satisfy_client_calls(tmp_path):
    _frontend(tmp_path, "fetch('/api/orders');\n")
    (tmp_path / "server.js").write_text(
        "const app = require('express')();\n"
        "app.get('/api/orders', (req, res) => res.json([]));\n",
        encoding="utf-8",
    )

    assert _check(tmp_path)["valid"]


def test_a_missing_express_route_is_reported(tmp_path):
    _frontend(tmp_path, "fetch('/api/orders');\n")
    (tmp_path / "server.js").write_text(
        "const app = require('express')();\n"
        "app.get('/api/health', (req, res) => res.json({}));\n",
        encoding="utf-8",
    )

    result = _check(tmp_path)

    assert not result["valid"]
    assert result["unreachable_calls"][0]["url"] == "/api/orders"


def test_spring_mappings_satisfy_client_calls(tmp_path):
    _frontend(tmp_path, "fetch('/api/tasks');\n")
    java = tmp_path / "src" / "main" / "java" / "com" / "example"
    java.mkdir(parents=True)
    (java / "TaskController.java").write_text(
        "@RestController\n"
        "public class TaskController {\n"
        "  @GetMapping(\"/api/tasks\")\n"
        "  public List<Task> all() { return null; }\n"
        "}\n",
        encoding="utf-8",
    )

    assert _check(tmp_path)["valid"]


# ── never break validation ──────────────────────────────────────────────────

# ── simple projects must not be touched ─────────────────────────────────────
#
# This is the only check added that can newly fail a project, so the small
# end of the range is pinned deliberately. A static page or a one-file script
# has no client/server split to disagree about.

def test_a_static_html_site_is_not_judged(tmp_path):
    (tmp_path / "index.html").write_text(
        "<html><body><a href='/about'>about</a>"
        "<script>document.querySelector('a')</script></body></html>",
        encoding="utf-8",
    )
    (tmp_path / "style.css").write_text("body { margin: 0 }", encoding="utf-8")

    result = _check(tmp_path)

    assert result["valid"] and result["skipped"]


def test_a_single_file_script_is_not_judged(tmp_path):
    (tmp_path / "main.py").write_text(
        "def add(a, b):\n    return a + b\n\nif __name__ == '__main__':\n    print(add(1, 2))\n",
        encoding="utf-8",
    )

    result = _check(tmp_path)

    assert result["valid"] and result["skipped"]


def test_a_static_page_calling_someone_elses_api_is_not_judged(tmp_path):
    """No server here, so the weather API is not ours to implement."""
    (tmp_path / "index.html").write_text(
        "<script>fetch('/v1/forecast').then(r => r.json())</script>", encoding="utf-8"
    )

    assert _check(tmp_path)["valid"]


def test_a_small_flask_app_with_a_template_passes(tmp_path):
    """The commonest simple full-stack shape: one file, one page, one endpoint."""
    (tmp_path / "app.py").write_text("""
from flask import Flask, jsonify, render_template
app = Flask(__name__)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/items", methods=["GET"])
def items():
    return jsonify([])
""", encoding="utf-8")
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "index.html").write_text(
        "<script>fetch('/api/items').then(r => r.json())</script>", encoding="utf-8"
    )

    result = _check(tmp_path)

    assert result["valid"], f"a correct small app must pass: {result}"


def test_plain_links_and_form_actions_are_not_calls(tmp_path):
    """An href is navigation, not an endpoint the server must expose."""
    (tmp_path / "app.py").write_text("""
from flask import Flask
app = Flask(__name__)

@app.route("/")
def index():
    return "hi"
""", encoding="utf-8")
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "index.html").write_text(
        "<a href='/about'>about</a><form action='/submit' method='post'></form>",
        encoding="utf-8",
    )

    assert _check(tmp_path)["valid"]


def test_an_empty_workspace_is_fine(tmp_path):
    assert _check(tmp_path)["valid"]


def test_unreadable_and_odd_files_do_not_raise(tmp_path):
    _backend(tmp_path, "from fastapi import FastAPI\napp = FastAPI()\n@app.get('/x')\ndef x(): ...\n")
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "weird.js").write_bytes(b"\xff\xfe fetch('/api/x')")

    _check(tmp_path)  # must not raise
