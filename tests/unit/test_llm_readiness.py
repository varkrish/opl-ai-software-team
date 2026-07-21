"""
TDD: LLM readiness for job create (BYOK or server fallback).

Jobs must not be created when neither the user's BYOK key nor the server
fallback key is available — otherwise they sit forever in Queued.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "agent"))
sys.path.insert(0, str(root / "agent" / "src"))

from crew_studio.job_database import JobDatabase
from crew_studio.llm_readiness import (
    LLM_NOT_CONFIGURED_CODE,
    llm_not_configured_payload,
    resolve_llm_readiness,
)


@pytest.fixture
def job_db():
    with tempfile.TemporaryDirectory() as tmp:
        yield JobDatabase(Path(tmp) / "jobs.db")


def _server_config(*, api_key: str = "", environment: str = "production"):
    return SimpleNamespace(
        llm=SimpleNamespace(
            api_key=api_key,
            environment=environment,
            api_base_url="https://example.com",
        )
    )


class TestResolveLlmReadiness:
    def test_none_when_no_byok_and_no_server(self, job_db, monkeypatch):
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        result = resolve_llm_readiness(job_db, "user-1", None)
        assert result["configured"] is False
        assert result["source"] == "none"
        assert "hint" in result

    def test_byok_wins_over_empty_server(self, job_db, monkeypatch):
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        job_db.save_llm_config(
            owner_id="user-1",
            api_base_url="https://byok.example",
            api_key="sk-byok-secret",
            model_manager="m",
            model_worker="w",
            model_reviewer="r",
        )
        result = resolve_llm_readiness(job_db, "user-1", _server_config(api_key=""))
        assert result == {"configured": True, "source": "byok"}

    def test_server_key_when_no_byok(self, job_db, monkeypatch):
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        result = resolve_llm_readiness(
            job_db, "user-1", _server_config(api_key="sk-server")
        )
        assert result == {"configured": True, "source": "server"}

    def test_env_llm_api_key_counts_as_server(self, job_db, monkeypatch):
        monkeypatch.setenv("LLM_API_KEY", "sk-from-env")
        result = resolve_llm_readiness(job_db, "user-1", None)
        assert result == {"configured": True, "source": "server"}

    def test_local_environment_does_not_require_key(self, job_db, monkeypatch):
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        result = resolve_llm_readiness(
            job_db, "user-1", _server_config(api_key="", environment="local")
        )
        assert result == {"configured": True, "source": "server"}

    def test_empty_byok_falls_through_to_server(self, job_db, monkeypatch):
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        # Simulate undecryptable/empty BYOK by stubbing get_llm_config
        job_db.get_llm_config = MagicMock(return_value={
            "api_key": "   ",
            "api_base_url": "https://x",
        })
        result = resolve_llm_readiness(
            job_db, "user-1", _server_config(api_key="sk-server")
        )
        assert result == {"configured": True, "source": "server"}

    def test_other_users_byok_is_ignored(self, job_db, monkeypatch):
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        job_db.save_llm_config(
            owner_id="other-user",
            api_base_url="https://byok.example",
            api_key="sk-other",
            model_manager="m",
            model_worker="w",
            model_reviewer="r",
        )
        result = resolve_llm_readiness(job_db, "user-1", _server_config(api_key=""))
        assert result["configured"] is False
        assert result["source"] == "none"


class TestNotConfiguredPayload:
    def test_payload_has_stable_code(self):
        payload = llm_not_configured_payload()
        assert payload["code"] == LLM_NOT_CONFIGURED_CODE
        assert "message" in payload
        assert "hint" in payload
