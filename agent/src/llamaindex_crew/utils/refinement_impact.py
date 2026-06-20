"""Impact-aware file scope discovery for refinement."""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

from ..tools.tldr_tools import read_call_graph


def _tokens_from_prompt(prompt: str) -> list[str]:
    prompt_no_urls = re.sub(r"https?://\S+", "", prompt)
    raw = re.findall(r"[`\"']([^`\"']{2,40})[`\"']", prompt_no_urls)
    raw += re.findall(r"\b([a-zA-Z_][a-zA-Z0-9_]{2,})\b", prompt_no_urls)
    stop = {
        "the", "this", "that", "and", "for", "with", "from", "file", "fix", "bug",
        "error", "when", "then", "should", "return", "true", "false", "null",
    }
    return list({t for t in raw if t.lower() not in stop})[:12]


def discover_impact_files(
    workspace_path: Path,
    prompt: str,
    primary_file: str,
    *,
    max_files: int = 5,
    all_source_files: Optional[List[str]] = None,
) -> list[str]:
    """Return primary file plus related files from call graph and prompt tokens."""
    workspace_path = Path(workspace_path)
    primary = primary_file.replace("\\", "/").lstrip("./")
    result: list[str] = [primary]
    seen = {primary}

    # Call graph neighbors
    for edge in read_call_graph(workspace_path):
        for key in ("from_file", "to_file"):
            fp = (edge.get(key) or "").replace("\\", "/")
            if not fp or fp in seen:
                continue
            if edge.get("from_file") == primary or edge.get("to_file") == primary:
                if (workspace_path / fp).exists():
                    seen.add(fp)
                    result.append(fp)

    # Prompt token co-occurrence in source files
    tokens = _tokens_from_prompt(prompt)
    if tokens and all_source_files:
        for fp in all_source_files:
            if fp in seen or len(result) >= max_files:
                continue
            try:
                content = (workspace_path / fp).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if any(tok in content for tok in tokens):
                seen.add(fp)
                result.append(fp)

    return result[:max_files]
