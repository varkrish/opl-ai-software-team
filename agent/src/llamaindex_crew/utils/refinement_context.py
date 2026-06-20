"""Load project context for refinement / fix agents."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .prompt_budget import PromptBudget, trim_text
from .rag_context import get_phase_rag_context

logger = logging.getLogger(__name__)

_ARTIFACT_FILES = (
    ("tech_stack.md", "tech_stack"),
    ("design_spec.md", "design_spec"),
    ("user_stories.md", "user_stories"),
    ("requirements.md", "requirements"),
    ("epic_progress.md", "epic_progress"),
)

_DEFAULT_INPUT_BUDGET = 12_000  # tokens for context sections


@dataclass
class RefinementContext:
    """Bundled project context for refinement prompts."""
    vision: str = ""
    sections: dict[str, str] = field(default_factory=dict)
    rag_context: str = ""
    code_structure: str = ""
    file_tree: str = ""

    def format_for_prompt(self, max_tokens: int = _DEFAULT_INPUT_BUDGET) -> str:
        """Render all sections into one markdown block, trimmed to budget."""
        budget = PromptBudget.from_context(
            context_window=max_tokens + 600,
            max_tokens=600,
        )
        # Override with explicit budget
        budget = PromptBudget(input_token_budget=max_tokens)
        parts = []
        if self.vision:
            parts.append("## Project vision\n" + self.vision)
        for label, key in [
            ("Tech stack", "tech_stack"),
            ("Design specification", "design_spec"),
            ("User stories", "user_stories"),
            ("Requirements", "requirements"),
            ("Epic progress", "epic_progress"),
        ]:
            text = self.sections.get(key, "")
            if text:
                parts.append(f"## {label}\n{text}")
        if self.rag_context:
            parts.append("## Relevant indexed context\n" + self.rag_context)
        if self.code_structure:
            parts.append("## Project structure (tldr)\n" + self.code_structure)
        if self.file_tree:
            parts.append("## File tree\n" + self.file_tree)

        combined = "\n\n".join(parts)
        if budget.fits(combined):
            return combined

        # Proportional trim of variable sections
        section_dict = {
            "vision": self.vision,
            **{k: self.sections.get(k, "") for k in ("tech_stack", "design_spec", "user_stories", "requirements", "epic_progress")},
            "rag": self.rag_context,
            "structure": self.code_structure,
            "tree": self.file_tree,
        }
        trimmed = budget.fit(
            section_dict,
            fixed_overhead_chars=200,
            priority=["vision", "tech_stack", "design_spec", "rag", "user_stories", "requirements", "epic_progress", "structure", "tree"],
        )
        out = []
        if trimmed.get("vision"):
            out.append("## Project vision\n" + trimmed["vision"])
        for label, key in [
            ("Tech stack", "tech_stack"),
            ("Design specification", "design_spec"),
            ("User stories", "user_stories"),
            ("Requirements", "requirements"),
            ("Epic progress", "epic_progress"),
        ]:
            if trimmed.get(key):
                out.append(f"## {label}\n" + trimmed[key])
        if trimmed.get("rag"):
            out.append("## Relevant indexed context\n" + trimmed["rag"])
        if trimmed.get("structure"):
            out.append("## Project structure (tldr)\n" + trimmed["structure"])
        if trimmed.get("tree"):
            out.append("## File tree\n" + trimmed["tree"])
        return "\n\n".join(out)


def _read_artifact(workspace_path: Path, name: str, max_chars: int = 24_000) -> str:
    p = workspace_path / name
    if not p.exists():
        return ""
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
        if len(text) > max_chars:
            return text[:max_chars] + "\n[... truncated ...]"
        return text
    except OSError as exc:
        logger.warning("Could not read %s: %s", name, exc)
        return ""


def _compact_file_tree(workspace_path: Path, max_lines: int = 80) -> str:
    lines: list[str] = []
    skip = {".git", "__pycache__", "node_modules", ".pytest_cache", ".venv", "venv", ".tldr"}
    try:
        for item in sorted(workspace_path.rglob("*")):
            if any(part in skip for part in item.parts):
                continue
            if not item.is_file():
                continue
            rel = str(item.relative_to(workspace_path))
            if rel.startswith(".") and rel not in ("README.md",):
                continue
            lines.append(rel)
            if len(lines) >= max_lines:
                lines.append("... (truncated)")
                break
    except OSError:
        pass
    return "\n".join(lines)


def load_refinement_context(
    workspace_path: Path,
    job_db: Any,
    job_id: str,
    *,
    user_prompt: str = "",
    max_context_tokens: int = _DEFAULT_INPUT_BUDGET,
) -> RefinementContext:
    """Load vision, artifacts, RAG, and optional tldr structure for refinement."""
    workspace_path = Path(workspace_path)
    ctx = RefinementContext()

    try:
        job = job_db.get_job(job_id) if job_db else None
        if job:
            ctx.vision = trim_text((job.get("vision") or "")[:8000], max_tokens=500)
    except Exception as exc:
        logger.warning("load_refinement_context: job lookup failed: %s", exc)

    for filename, key in _ARTIFACT_FILES:
        ctx.sections[key] = _read_artifact(workspace_path, filename)

    ctx.file_tree = _compact_file_tree(workspace_path)

    try:
        from llamaindex_crew.utils.document_indexer import DocumentIndexer
        indexer = DocumentIndexer(workspace_path, job_id)
        if getattr(indexer, "has_index", False):
            ctx.rag_context = get_phase_rag_context(
                indexer,
                "development",
                extra_query=user_prompt[:500],
            )
    except Exception as exc:
        logger.debug("RAG context unavailable for refinement: %s", exc)

    try:
        from llamaindex_crew.tools.tldr_tools import detect_tldr_lang, _code_structure
        lang = detect_tldr_lang(workspace_path)
        structure = _code_structure(workspace= str(workspace_path), lang=lang)
        if structure and not structure.startswith("tldr is not installed"):
            ctx.code_structure = trim_text(structure, max_tokens=1500)
    except Exception as exc:
        logger.debug("tldr structure skipped: %s", exc)

    return ctx
