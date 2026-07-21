"""LLM credential readiness for job creation (BYOK or server fallback)."""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

LLM_NOT_CONFIGURED_CODE = "llm_not_configured"

_HINT = (
    "Save a key in Settings → API Configuration, or set llm.api_key in "
    "config.yaml / LLM_API_KEY for the server fallback."
)


def _normalize_api_key(api_key: Any) -> str:
    if api_key is None:
        return ""
    if isinstance(api_key, bytes):
        api_key = api_key.decode("utf-8", errors="replace")
    return str(api_key).strip()


def _server_key_usable(server_config: Any) -> bool:
    """True when server config allows LLM calls without a BYOK key."""
    if server_config is None:
        return False
    llm = getattr(server_config, "llm", None)
    if llm is None:
        return False
    env = (getattr(llm, "environment", None) or "").lower()
    if env == "local":
        return True
    return bool(_normalize_api_key(getattr(llm, "api_key", None)))


def resolve_llm_readiness(
    job_db: Any,
    owner_id: Optional[str],
    server_config: Any = None,
) -> Dict[str, Any]:
    """Return whether the owner can start LLM jobs.

    Resolution order matches ``user_llm_context`` / ``ensure_llm_api_key``:
      1. BYOK for ``owner_id`` (non-empty decrypted key)
      2. Server SecretConfig (or local/Ollama mode)
      3. ``LLM_API_KEY`` environment variable (legacy server fallback)
    """
    if owner_id:
        try:
            user_llm = job_db.get_llm_config(owner_id)
        except Exception:
            user_llm = None
        if user_llm and _normalize_api_key(user_llm.get("api_key")):
            return {"configured": True, "source": "byok"}

    if _server_key_usable(server_config):
        return {"configured": True, "source": "server"}

    if _normalize_api_key(os.getenv("LLM_API_KEY")):
        return {"configured": True, "source": "server"}

    return {
        "configured": False,
        "source": "none",
        "hint": _HINT,
    }


def llm_not_configured_payload(hint: Optional[str] = None) -> Dict[str, str]:
    """Stable error body for HTTP 422 on job create."""
    return {
        "code": LLM_NOT_CONFIGURED_CODE,
        "message": (
            "No LLM API key configured. Configure a key before creating a job."
        ),
        "hint": hint or _HINT,
    }
