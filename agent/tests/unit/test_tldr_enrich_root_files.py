"""
Observed root-level sources must reach the contract, and dependencies must not.

``enrich_wiring_contract_from_tldr`` overlays what is actually on disk onto the
contract. When a file belonged to no declared package it derived one from the
parent directory — and skipped anything whose parent was ``"."``::

    parent = str(Path(fp).parent)
    if parent and parent != ".":
        pkg = parent
        packages.setdefault(pkg, ...)
    if not pkg:
        continue

That was the fourth appearance of the same root-level exclusion, after the
contract seed dropping flat-layout sources, every language strategy missing
``backend/requirements.txt``, and preview finding no entrypoint below the
workspace root. Each assumed a project is nested; flat is the norm for the
small-to-mid projects this pipeline produces.

The consequence here is narrower than the seed's but the same shape: a
root-level file created *after* the contract was locked — by gap-fill, or by the
fix loop — is observed by tldr, matched to nothing, and dropped, leaving the
contract blind to a file it will later be asked to reconcile.

Second defect in the same loop: no vendor filtering. Anything tldr reported out
of node_modules or a virtualenv became a package in the project's own contract.
Fixed with the shared ``vendor_paths`` helper rather than an eleventh private
list.
"""
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from llamaindex_crew.utils import wiring_contract as wc  # noqa: E402


def _contract():
    return {
        "version": 1,
        "module": "demo",
        "language": "python",
        "packages": {"app": {"files": ["app/service.py"], "owns": []}},
        "symbols": {},
        "deps": [],
    }


@pytest.fixture
def fake_tldr(monkeypatch):
    """Drive the overlay from a structure payload without needing the binary."""
    def _install(files):
        import llamaindex_crew.tools.tldr_tools as tldr_tools

        monkeypatch.setattr(tldr_tools, "_resolve_tldr_bin", lambda: "/usr/bin/true", raising=False)
        monkeypatch.setattr(tldr_tools, "_workspace_has_indexable_source", lambda ws: True, raising=False)
        monkeypatch.setattr(tldr_tools, "read_call_graph", lambda ws: [], raising=False)
        monkeypatch.setattr(tldr_tools, "detect_tldr_lang", lambda ws: "python", raising=False)

        class _Result:
            returncode = 0
            stdout = json.dumps({"files": files})
            stderr = ""

        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Result())
    return _install


def _declared(contract):
    return {f for p in (contract.get("packages") or {}).values() for f in (p.get("files") or [])}


# ── the root-level exclusion ────────────────────────────────────────────────

def test_a_root_level_source_reaches_the_contract(tmp_path, fake_tldr):
    (tmp_path / "main.py").write_text("def run(): pass\n", encoding="utf-8")
    fake_tldr([{"path": "main.py", "functions": ["run"], "classes": []}])

    out = wc.enrich_wiring_contract_from_tldr(tmp_path, _contract())

    assert "main.py" in _declared(out), (
        f"a flat project's observed source was dropped: {out['packages']}"
    )


def test_its_symbols_are_recorded_too(tmp_path, fake_tldr):
    (tmp_path / "main.py").write_text("def run(): pass\n", encoding="utf-8")
    fake_tldr([{"path": "main.py", "functions": ["run"], "classes": []}])

    out = wc.enrich_wiring_contract_from_tldr(tmp_path, _contract())

    assert any(k.endswith(".run") or k == "run" for k in out.get("symbols", {})), (
        f"symbols went with the dropped file: {out.get('symbols')}"
    )


def test_nested_files_keep_working(tmp_path, fake_tldr):
    (tmp_path / "app").mkdir()
    fake_tldr([{"path": "app/extra.py", "functions": ["helper"], "classes": []}])

    out = wc.enrich_wiring_contract_from_tldr(tmp_path, _contract())

    assert "app/extra.py" in _declared(out)


def test_declared_packages_are_still_preferred(tmp_path, fake_tldr):
    """A file already owned must not be re-homed to its parent directory."""
    (tmp_path / "app").mkdir()
    fake_tldr([{"path": "app/service.py", "functions": ["svc"], "classes": []}])

    out = wc.enrich_wiring_contract_from_tldr(tmp_path, _contract())

    assert "app/service.py" in out["packages"]["app"]["files"]


# ── vendored code is not this project ───────────────────────────────────────

def test_vendored_sources_never_become_project_packages(tmp_path, fake_tldr):
    fake_tldr([
        {"path": "node_modules/left-pad/index.js", "functions": ["leftPad"], "classes": []},
        {"path": "frontend/node_modules/react/index.js", "functions": ["createElement"], "classes": []},
        {"path": ".venv/lib/python3.11/site-packages/requests/api.py", "functions": ["get"], "classes": []},
        {"path": "main.py", "functions": ["run"], "classes": []},
    ])
    (tmp_path / "main.py").write_text("def run(): pass\n", encoding="utf-8")

    out = wc.enrich_wiring_contract_from_tldr(tmp_path, _contract())

    for pkg in out["packages"]:
        assert "node_modules" not in pkg, f"vendored package leaked in: {pkg}"
        assert "site-packages" not in pkg and ".venv" not in pkg, f"leaked: {pkg}"
    assert "main.py" in _declared(out), "the project's own file must survive the filter"


def test_a_lookalike_directory_is_not_treated_as_vendored(tmp_path, fake_tldr):
    """The substring check this replaces called my-venv-tool a virtualenv."""
    fake_tldr([{"path": "my-venv-tool/main.py", "functions": ["run"], "classes": []}])

    out = wc.enrich_wiring_contract_from_tldr(tmp_path, _contract())

    assert "my-venv-tool/main.py" in _declared(out)


def test_a_missing_tldr_binary_is_a_noop(tmp_path, monkeypatch):
    import llamaindex_crew.tools.tldr_tools as tldr_tools
    monkeypatch.setattr(tldr_tools, "_resolve_tldr_bin", lambda: None, raising=False)
    monkeypatch.setattr(tldr_tools, "_workspace_has_indexable_source", lambda ws: True, raising=False)

    before = _contract()
    out = wc.enrich_wiring_contract_from_tldr(tmp_path, before)

    assert out["packages"] == before["packages"]
