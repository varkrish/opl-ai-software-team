"""
Document Indexer for RAG capabilities.

Indexes project artifacts and reference documents with explicit chunking so large
plans are retrieved semantically instead of truncated inline in prompts.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

from llama_index.core import Document, Settings, StorageContext, VectorStoreIndex, load_index_from_storage
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import BaseNode

try:
    from llama_index.embeddings.huggingface import HuggingFaceEmbedding
except ImportError:
    HuggingFaceEmbedding = None

logger = logging.getLogger(__name__)

DEFAULT_CHUNK_SIZE = 1024
DEFAULT_CHUNK_OVERLAP = 128
DEFAULT_RAG_TOP_K = 6
DEFAULT_MAX_RAG_CONTEXT_CHARS = 32_000


@dataclass
class RetrievedChunk:
    """A single retrieved text chunk with source metadata."""
    text: str
    source: str
    chunk_index: int = 0
    score: Optional[float] = None


def _init_embeddings() -> None:
    try:
        if HuggingFaceEmbedding:
            Settings.embed_model = HuggingFaceEmbedding(model_name="BAAI/bge-small-en-v1.5")
        else:
            logger.warning("llama-index-embeddings-huggingface not installed, falling back to default")
    except Exception as e:
        logger.warning("Could not initialize local embeddings: %s", e)


def format_retrieved_chunks(chunks: Sequence[RetrievedChunk], max_chars: int = DEFAULT_MAX_RAG_CONTEXT_CHARS) -> str:
    """Format retrieved chunks for prompt injection, respecting a total char budget."""
    if not chunks:
        return ""
    parts: List[str] = []
    total = 0
    for i, chunk in enumerate(chunks):
        header = f"--- [{chunk.source}] chunk {chunk.chunk_index + 1} ---"
        block = f"{header}\n{chunk.text.strip()}"
        if total + len(block) > max_chars:
            remaining = max_chars - total
            if remaining > 200:
                parts.append(block[:remaining] + "\n... (retrieval budget reached)")
            break
        parts.append(block)
        total += len(block) + 2
    return "\n\n".join(parts)


class DocumentIndexer:
    """Indexes project artifacts for RAG retrieval with explicit chunking."""

    MANIFEST_NAME = "rag_index_manifest.json"

    def __init__(
        self,
        workspace_path: Path,
        project_id: str,
        *,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    ):
        self.workspace_path = Path(workspace_path)
        self.project_id = project_id
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.index: Optional[VectorStoreIndex] = None
        self.index_path = self.workspace_path / f"index_{project_id}"
        self._splitter = SentenceSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        self._indexed_sources: List[str] = []

        _init_embeddings()
        self._try_load_persisted_index()

    def _try_load_persisted_index(self) -> None:
        if not self.index_path.is_dir():
            return
        try:
            storage = StorageContext.from_defaults(persist_dir=str(self.index_path))
            self.index = load_index_from_storage(storage)
            manifest = self.workspace_path / self.MANIFEST_NAME
            if manifest.is_file():
                data = json.loads(manifest.read_text(encoding="utf-8"))
                self._indexed_sources = list(data.get("sources", []))
            logger.info("Loaded persisted RAG index from %s (%d sources)", self.index_path, len(self._indexed_sources))
        except Exception as e:
            logger.warning("Could not load persisted index at %s: %s", self.index_path, e)
            self.index = None

    def _persist_index(self) -> None:
        if self.index is None:
            return
        try:
            self.index_path.mkdir(parents=True, exist_ok=True)
            self.index.storage_context.persist(persist_dir=str(self.index_path))
            manifest = {
                "project_id": self.project_id,
                "sources": self._indexed_sources,
                "chunk_size": self.chunk_size,
                "chunk_overlap": self.chunk_overlap,
            }
            (self.workspace_path / self.MANIFEST_NAME).write_text(
                json.dumps(manifest, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("Could not persist RAG index: %s", e)

    def _documents_to_nodes(self, documents: List[Document]) -> List[BaseNode]:
        return self._splitter.get_nodes_from_documents(documents)

    def _insert_documents(self, documents: List[Document]) -> int:
        if not documents:
            return 0
        nodes = self._documents_to_nodes(documents)
        if not nodes:
            return 0
        if self.index is None:
            self.index = VectorStoreIndex(nodes)
        else:
            self.index.insert_nodes(nodes)
        return len(nodes)

    def index_text(
        self,
        text: str,
        source: str,
        *,
        doc_type: str = "reference",
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Index raw text under *source* label; returns number of chunks inserted."""
        if not text or not text.strip():
            return 0
        metadata: Dict[str, Any] = {
            "file_path": source,
            "project_id": self.project_id,
            "doc_type": doc_type,
            "source": source,
        }
        if extra_metadata:
            metadata.update(extra_metadata)
        doc = Document(text=text, metadata=metadata)
        count = self._insert_documents([doc])
        if source not in self._indexed_sources:
            self._indexed_sources.append(source)
        logger.debug("Indexed %d chunk(s) from source %r", count, source)
        return count

    def index_file_at_path(
        self,
        file_path: Union[str, Path],
        *,
        source_label: Optional[str] = None,
        doc_type: str = "reference",
    ) -> int:
        """Read and index a file from an absolute or workspace-relative path."""
        path = Path(file_path)
        if not path.is_file():
            logger.warning("Reference file not found: %s", file_path)
            return 0
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            logger.warning("Could not read %s: %s", file_path, e)
            return 0
        label = source_label or path.name
        return self.index_text(
            content,
            label,
            doc_type=doc_type,
            extra_metadata={"file_type": path.suffix, "absolute_path": str(path.resolve())},
        )

    def index_artifacts(self, artifact_files: List[str]) -> None:
        """Index project artifacts (relative paths under workspace)."""
        total = 0
        for file_path in artifact_files:
            full_path = self.workspace_path / file_path
            if not full_path.exists():
                logger.warning("Artifact file not found: %s", file_path)
                continue
            total += self.index_file_at_path(full_path, source_label=file_path, doc_type="artifact")
        if total:
            self._persist_index()
            logger.info("Indexed %d chunk(s) from %d artifact file(s)", total, len(artifact_files))

    def retrieve(
        self,
        query_text: str,
        top_k: int = DEFAULT_RAG_TOP_K,
        *,
        max_chars: int = DEFAULT_MAX_RAG_CONTEXT_CHARS,
    ) -> List[RetrievedChunk]:
        """Retrieve top-k relevant chunks (no LLM synthesis — raw nodes only)."""
        if self.index is None or not query_text.strip():
            return []
        try:
            retriever = self.index.as_retriever(similarity_top_k=top_k)
            scored_nodes = retriever.retrieve(query_text)
            chunks: List[RetrievedChunk] = []
            seen: set[str] = set()
            for item in scored_nodes:
                score = getattr(item, "score", None)
                node = getattr(item, "node", item)
                if hasattr(node, "get_content"):
                    text = node.get_content()
                else:
                    text = getattr(node, "text", "") or ""
                if not text or not str(text).strip():
                    continue
                meta = getattr(node, "metadata", {}) or {}
                source = str(meta.get("source") or meta.get("file_path") or "unknown")
                chunk_idx = int(meta.get("chunk_index") or 0)
                dedupe_key = f"{source}:{hash(str(text)[:200])}"
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                chunks.append(
                    RetrievedChunk(
                        text=str(text).strip(),
                        source=source,
                        chunk_index=chunk_idx,
                        score=float(score) if score is not None else None,
                    )
                )
            if max_chars and chunks:
                formatted_len = 0
                trimmed: List[RetrievedChunk] = []
                for c in chunks:
                    block_len = len(c.text) + len(c.source) + 40
                    if formatted_len + block_len > max_chars:
                        break
                    trimmed.append(c)
                    formatted_len += block_len
                return trimmed
            return chunks
        except Exception as e:
            logger.error("RAG retrieve failed: %s", e)
            return []

    def retrieve_formatted(
        self,
        query_text: str,
        top_k: int = DEFAULT_RAG_TOP_K,
        *,
        max_chars: int = DEFAULT_MAX_RAG_CONTEXT_CHARS,
    ) -> str:
        """Retrieve and format chunks for prompt injection."""
        chunks = self.retrieve(query_text, top_k=top_k, max_chars=max_chars)
        return format_retrieved_chunks(chunks, max_chars=max_chars)

    def query(self, query_text: str, top_k: int = 3) -> List[str]:
        """Backward-compatible API: return chunk texts only."""
        return [c.text for c in self.retrieve(query_text, top_k=top_k)]

    def finalize(self) -> None:
        """Persist index after batch indexing."""
        self._persist_index()

    def index_default_artifacts(self) -> None:
        """Index default project artifacts."""
        default_files = [
            "requirements.md",
            "user_stories.md",
            "design_spec.md",
            "tech_stack.md",
        ]
        existing_files = [f for f in default_files if (self.workspace_path / f).exists()]
        if existing_files:
            self.index_artifacts(existing_files)
        else:
            logger.info("No default artifacts found to index")

    @property
    def has_index(self) -> bool:
        return self.index is not None

    @property
    def source_count(self) -> int:
        return len(self._indexed_sources)
