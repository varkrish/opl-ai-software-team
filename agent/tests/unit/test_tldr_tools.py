"""Tests for llm-tldr binary resolution (avoid tealdeer PATH conflict)."""
from pathlib import Path
from unittest.mock import patch

from llamaindex_crew.tools import tldr_tools


def test_resolve_tldr_bin_uses_tldr_bin_env(monkeypatch, tmp_path):
    custom = tmp_path / "custom-tldr"
    custom.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    custom.chmod(0o755)

    tldr_tools._TLDR_BIN_CACHE = None
    monkeypatch.setenv("TLDR_BIN", str(custom))
    monkeypatch.setattr(tldr_tools, "_is_llm_tldr", lambda _b: True)

    assert tldr_tools._resolve_tldr_bin() == str(custom)


def test_resolve_tldr_bin_skips_tealdeer_on_path(monkeypatch, tmp_path):
    tealdeer = tmp_path / "tealdeer"
    llm = tmp_path / "llm-tldr"
    for p in (tealdeer, llm):
        p.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
        p.chmod(0o755)

    venv_dir = tmp_path / "venv" / "bin"
    venv_dir.mkdir(parents=True)
    venv_tldr = venv_dir / "tldr"
    venv_tldr.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    venv_tldr.chmod(0o755)
    (venv_dir / "python3").write_text("", encoding="utf-8")

    tldr_tools._TLDR_BIN_CACHE = None
    monkeypatch.delenv("TLDR_BIN", raising=False)

    def fake_is_llm_tldr(binary: str) -> bool:
        return Path(binary) == venv_tldr

    monkeypatch.setattr(tldr_tools, "_is_llm_tldr", fake_is_llm_tldr)
    monkeypatch.setattr(tldr_tools.shutil, "which", lambda _name: str(tealdeer))
    monkeypatch.setattr(tldr_tools.sys, "executable", str(venv_dir / "python3"))

    assert tldr_tools._resolve_tldr_bin() == str(venv_tldr)
    tldr_tools._TLDR_BIN_CACHE = None


def test_refresh_call_graph_invokes_tldr_warm(monkeypatch, tmp_path):
    calls = []

    def mock_run_tldr(args):
        calls.append(args)
        return "Processed python: 1 files"

    monkeypatch.setattr(tldr_tools, "_run_tldr", mock_run_tldr)

    ws = tmp_path / "workspace"
    ws.mkdir()

    tldr_tools.refresh_call_graph(ws)
    assert len(calls) == 1
    assert calls[0] == ["warm", str(ws)]

    calls.clear()
    tldr_tools.refresh_call_graph(ws, lang="python")
    assert len(calls) == 1
    assert calls[0] == ["warm", str(ws), "--lang", "python"]


def test_call_graph_end_to_end_fixture(tmp_path):
    tldr_tools._TLDR_BIN_CACHE = None
    tldr_bin = tldr_tools._resolve_tldr_bin()
    if not tldr_bin:
        import pytest
        pytest.skip("tldr binary not available in environment")

    ws = tmp_path / "workspace"
    app_dir = ws / "app"
    calc_dir = ws / "calc"
    app_dir.mkdir(parents=True)
    calc_dir.mkdir(parents=True)

    (app_dir / "__init__.py").write_text("", encoding="utf-8")
    (calc_dir / "__init__.py").write_text("", encoding="utf-8")
    (calc_dir / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (app_dir / "main.py").write_text("from calc.calc import add\n\ndef main():\n    return add(1, 2)\n", encoding="utf-8")

    # 1. Warm call graph cache
    tldr_tools.refresh_call_graph(ws)

    cache_file = ws / ".tldr" / "cache" / "call_graph.json"
    assert cache_file.exists(), ".tldr/cache/call_graph.json should be created by refresh_call_graph"

    # 2. Read call graph edges
    edges = tldr_tools.read_call_graph(ws)
    assert len(edges) > 0, "read_call_graph should return non-empty edges for fixture with calls"
    assert any(e.get("from_func") == "main" and e.get("to_func") == "add" for e in edges)

    # 3. Enrich wiring contract and verify non-empty deps
    from llamaindex_crew.utils.wiring_contract import enrich_wiring_contract_from_tldr
    contract = {
        "language": "python",
        "packages": {
            "app": {"files": ["app/__init__.py", "app/main.py"], "owns": []},
            "calc": {"files": ["calc/__init__.py", "calc/calc.py"], "owns": []},
        },
        "deps": [],
    }
    enriched = enrich_wiring_contract_from_tldr(ws, contract)
    assert len(enriched.get("deps", [])) > 0, "Wiring contract deps must be populated from call graph edges"
    assert any(d.get("from") == "app" and d.get("to") == "calc" for d in enriched["deps"])

    # 4. Verify _capture_code_graph captures edges and non-empty output
    from llamaindex_crew.utils.document_indexer import _capture_code_graph
    cg_content = _capture_code_graph(ws)
    assert cg_content is not None
    import json
    parsed = json.loads(cg_content)
    assert "edges" in parsed
    assert len(parsed["edges"]) > 0


def test_capture_code_graph_empty_when_no_edges(tmp_path):
    tldr_bin = tldr_tools._resolve_tldr_bin()
    if not tldr_bin:
        import pytest
        pytest.skip("tldr binary not available in environment")

    ws = tmp_path / "empty_workspace"
    app_dir = ws / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "alone.py").write_text("x = 42\n", encoding="utf-8")

    from llamaindex_crew.utils.document_indexer import _capture_code_graph
    cg_content = _capture_code_graph(ws)
    assert cg_content is None, "Should return None and store nothing if call graph is empty"

