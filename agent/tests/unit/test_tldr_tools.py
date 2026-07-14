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
