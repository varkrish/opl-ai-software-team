"""Merge per-user workflow preferences into SecretConfig at job runtime."""
from __future__ import annotations

from typing import Any, Dict, Optional

DEFAULT_WORKFLOW_PREFS: Dict[str, Any] = {
    "plan_review_enabled": False,
    "solutioning_enabled": False,
    "solutioning_mode": "full",
    "solutioning_max_passes": 3,
    "solutioning_max_github_searches": 10,
    "auto_approve_plan": False,
    "tldr_enabled": True,
    "tldr_max_chars": 6000,
    "tldr_include_structure": True,
    "tldr_min_completed_files": 1,
    "parallel_file_workers": 2,
}


def normalize_workflow_prefs(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return a full prefs dict with defaults applied."""
    merged = dict(DEFAULT_WORKFLOW_PREFS)
    if not raw:
        return merged
    for key in DEFAULT_WORKFLOW_PREFS:
        if key in raw and raw[key] is not None:
            merged[key] = raw[key]
    merged["plan_review_enabled"] = bool(merged["plan_review_enabled"])
    merged["solutioning_enabled"] = bool(merged["solutioning_enabled"])
    merged["auto_approve_plan"] = bool(merged["auto_approve_plan"])
    mode = str(merged.get("solutioning_mode") or "full").strip().lower()
    if mode not in ("full", "fast", "adaptive"):
        mode = "full"
    merged["solutioning_mode"] = mode
    merged["solutioning_max_passes"] = max(1, min(5, int(merged["solutioning_max_passes"])))
    merged["solutioning_max_github_searches"] = max(1, min(50, int(merged["solutioning_max_github_searches"])))
    merged["tldr_enabled"] = bool(merged["tldr_enabled"])
    merged["tldr_include_structure"] = bool(merged["tldr_include_structure"])
    merged["tldr_max_chars"] = max(500, min(50_000, int(merged["tldr_max_chars"])))
    merged["tldr_min_completed_files"] = max(0, min(100, int(merged["tldr_min_completed_files"])))
    merged["parallel_file_workers"] = max(1, min(10, int(merged["parallel_file_workers"])))
    return merged


def normalize_capability_profile_metadata(
    meta: Optional[Dict[str, Any]],
    capability_profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Ensure job metadata carries capability_profile with default path=full."""
    out = dict(meta) if meta else {}
    profile = capability_profile
    if profile is None:
        profile = out.get("capability_profile")
    if not isinstance(profile, dict):
        profile = {}
    path = (profile.get("solutioning_path") or "full")
    if isinstance(path, str):
        path = path.strip().lower()
    if path not in ("full", "fast", "adaptive"):
        path = "full"
    normalized = {
        **profile,
        "solutioning_path": path,
        "source": profile.get("source") or ("user" if capability_profile else "default"),
    }
    out["capability_profile"] = normalized
    return out


def merge_workflow_prefs_into_config(config: Any, prefs: Optional[Dict[str, Any]]) -> Any:
    """Overlay user workflow prefs onto server SecretConfig (returns same object if no prefs)."""
    if config is None or not prefs:
        return config
    normalized = normalize_workflow_prefs(prefs)
    plan_review = config.plan_review.model_copy(
        update={"enabled": normalized["plan_review_enabled"]}
    )
    solutioning = config.solutioning.model_copy(
        update={
            "enabled": normalized["solutioning_enabled"],
            "mode": normalized.get("solutioning_mode", "full"),
            "max_passes": normalized["solutioning_max_passes"],
            "max_github_searches": normalized["solutioning_max_github_searches"],
        }
    )
    generation = config.generation.model_copy(
        update={
            "simple_mode_tldr_enabled": normalized["tldr_enabled"],
            "simple_mode_tldr_max_chars": normalized["tldr_max_chars"],
            "simple_mode_tldr_include_structure": normalized["tldr_include_structure"],
            "simple_mode_tldr_min_completed_files": normalized["tldr_min_completed_files"],
            "parallel_file_workers": normalized["parallel_file_workers"],
        }
    )
    return config.model_copy(
        update={
            "plan_review": plan_review,
            "solutioning": solutioning,
            "generation": generation,
        }
    )


def config_for_job_owner(config: Any, job_db: Any, owner_id: Optional[str]) -> Any:
    """Load owner workflow prefs and dynamic MCP tools from job_db and merge into config."""
    if not config:
        return config

    merged_config = config

    if job_db and owner_id:
        # 1. Merge workflow preferences
        prefs = job_db.get_workflow_config(owner_id)
        if prefs:
            merged_config = merge_workflow_prefs_into_config(merged_config, prefs)

        # 2. Merge dynamic MCP configurations
        mcp_configs = job_db.get_mcp_configs(owner_id)
        if mcp_configs:
            from src.llamaindex_crew.config.secure_config import McpToolEntry

            global_tools = list(merged_config.tools.global_tools)
            agent_tools = {k: list(v) for k, v in merged_config.tools.agent_tools.items()}

            for mcp in mcp_configs:
                entry = McpToolEntry(
                    type="mcp",
                    server_name=mcp["server_name"],
                    command=mcp.get("command"),
                    args=mcp.get("args") or [],
                    url=mcp.get("url"),
                    env=mcp.get("env") or {},
                    tools=mcp.get("tools") or [],
                )

                target = mcp.get("target_agent", "global") or "global"
                if target == "global":
                    if not any(getattr(t, "server_name", None) == entry.server_name for t in global_tools):
                        global_tools.append(entry)
                else:
                    if target not in agent_tools:
                        agent_tools[target] = []
                    if not any(getattr(t, "server_name", None) == entry.server_name for t in agent_tools[target]):
                        agent_tools[target].append(entry)

            tools_config = merged_config.tools.model_copy(
                update={"global_tools": global_tools, "agent_tools": agent_tools}
            )
            merged_config = merged_config.model_copy(update={"tools": tools_config})

    return merged_config
