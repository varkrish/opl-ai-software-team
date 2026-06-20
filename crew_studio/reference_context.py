"""
Reference document indexing and RAG context for build jobs.

Indexes uploaded reference docs without truncation; agents retrieve relevant
chunks per phase instead of relying on a single enriched-vision blob.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.llamaindex_crew.utils.document_indexer import DocumentIndexer
from src.llamaindex_crew.utils.rag_context import get_phase_rag_context

logger = logging.getLogger(__name__)

TEXT_TYPES = frozenset({
    "txt", "md", "json", "yaml", "yml", "csv", "xml",
    "py", "js", "ts", "java", "go", "rs", "rb", "sh",
    "html", "css", "sql", "proto", "graphql",
})


def _prompt_limits(config: Any) -> Any:
    return getattr(config, "prompt_limits", None) if config else None


def _use_rag_for_references(config: Any) -> bool:
    pl = _prompt_limits(config)
    if pl is None:
        return True
    return bool(getattr(pl, "use_rag_for_references", True))


def index_job_reference_documents(
    workspace_path: Path,
    job_id: str,
    uploaded_docs: List[Dict[str, Any]],
    config: Any = None,
) -> Dict[str, Any]:
    """
    Index all uploaded reference documents (full content, chunked).

    Returns manifest dict with indexed file count and chunk count.
    """
    workspace_path = Path(workspace_path)
    pl = _prompt_limits(config)
    chunk_size = int(getattr(pl, "rag_chunk_size", 1024)) if pl else 1024
    chunk_overlap = int(getattr(pl, "rag_chunk_overlap", 128)) if pl else 128

    indexer = DocumentIndexer(
        workspace_path,
        job_id,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )

    total_chunks = 0
    indexed_files: List[str] = []

    for doc in uploaded_docs:
        doc_path = Path(doc.get("stored_path", ""))
        file_type = (doc.get("file_type") or "").lower()
        if not doc_path.is_file() or file_type not in TEXT_TYPES:
            continue
        original = doc.get("original_name") or doc_path.name
        is_repomix = original.startswith("github:")
        label = original.replace("github:", "repo:") if is_repomix else original
        try:
            n = indexer.index_file_at_path(
                doc_path,
                source_label=f"reference:{label}",
                doc_type="repomix" if is_repomix else "reference",
            )
            if n:
                total_chunks += n
                indexed_files.append(label)
        except Exception as e:
            logger.warning("Failed to index reference doc %s: %s", original, e)

    if total_chunks:
        indexer.finalize()

    manifest = {
        "job_id": job_id,
        "indexed_files": indexed_files,
        "chunk_count": total_chunks,
        "rag_enabled": total_chunks > 0,
    }
    try:
        (workspace_path / "reference_index_manifest.json").write_text(
            json.dumps(manifest, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        logger.warning("Could not write reference_index_manifest.json: %s", e)

    logger.info(
        "Reference RAG index: %d file(s), %d chunk(s) for job %s",
        len(indexed_files), total_chunks, job_id,
    )
    return manifest


def build_pipeline_vision(
    base_vision: str,
    uploaded_docs: List[Dict[str, Any]],
    config: Any,
    rag_manifest: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Build vision string passed into SoftwareDevWorkflow.

    When RAG indexing succeeded, pass base vision plus a short note — not inline
    truncated docs. Otherwise fall back to inline merge with generous limits.
    """
    rag_manifest = rag_manifest or {}
    if rag_manifest.get("rag_enabled") and _use_rag_for_references(config):
        n_files = len(rag_manifest.get("indexed_files", []))
        n_chunks = rag_manifest.get("chunk_count", 0)
        return (
            f"{base_vision.strip()}\n\n"
            f"=== REFERENCE DOCUMENTS (RAG indexed) ===\n"
            f"{n_files} reference file(s) indexed as {n_chunks} searchable chunk(s). "
            f"Relevant sections are retrieved automatically per workflow phase — "
            f"use all retrieved context; do not ignore reference material.\n"
        )

    pl = _prompt_limits(config)
    max_ref_doc = int(getattr(pl, "max_reference_doc_chars", 50_000)) if pl else 50_000
    max_ref_repomix = int(getattr(pl, "max_reference_doc_chars_repomix", 200_000)) if pl else 200_000
    max_enriched = int(getattr(pl, "max_enriched_vision_chars", 100_000)) if pl else 100_000

    enriched = base_vision.strip()
    doc_parts: List[str] = []
    repo_parts: List[str] = []

    for doc in uploaded_docs:
        doc_path = Path(doc.get("stored_path", ""))
        file_type = (doc.get("file_type") or "").lower()
        if not doc_path.is_file() or file_type not in TEXT_TYPES:
            continue
        try:
            is_repomix = (doc.get("original_name") or "").startswith("github:")
            max_chars = max_ref_repomix if is_repomix else max_ref_doc
            content = doc_path.read_text(encoding="utf-8", errors="replace")[:max_chars]
            original = doc.get("original_name") or doc_path.name
            if is_repomix:
                repo_parts.append(
                    f"--- Reference Repository: {original.replace('github:', '')} ---\n{content}"
                )
            else:
                doc_parts.append(f"--- Reference: {original} ---\n{content}")
        except OSError as e:
            logger.warning("Could not read doc for inline fallback: %s", e)

    sections: List[str] = []
    if doc_parts:
        sections.append(
            f"=== REFERENCE DOCUMENTS ({len(doc_parts)} files) ===\n" + "\n\n".join(doc_parts)
        )
    if repo_parts:
        sections.append(
            f"=== REFERENCE REPOSITORIES ({len(repo_parts)} repos) ===\n"
            + "\n\n".join(repo_parts)
        )
    if sections:
        enriched = enriched + "\n\n" + "\n\n".join(sections)
        if len(enriched) > max_enriched:
            logger.warning(
                "Inline enriched vision truncated from %d to %d chars (max_enriched_vision_chars)",
                len(enriched), max_enriched,
            )
            enriched = enriched[:max_enriched] + "\n\n[... reference truncated — enable RAG indexing ...]"
    return enriched


__all__ = [
    "build_pipeline_vision",
    "get_phase_rag_context",
    "index_job_reference_documents",
]
