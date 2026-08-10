"""
Document Indexer for RAG capabilities.

Indexes project artifacts and reference documents with explicit chunking so large
plans are retrieved semantically instead of truncated inline in prompts.
"""
from __future__ import annotations

import json
import os
import subprocess
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

from llama_index.core import Document, Settings, StorageContext, VectorStoreIndex, load_index_from_storage
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import BaseNode

try:
    from llama_index.embeddings.huggingface import HuggingFaceEmbedding
except ImportError:
    HuggingFaceEmbedding = None

from llamaindex_crew.memory.scope import MemoryScope, slugify

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
    created_at: Optional[str] = None
    job_id: Optional[str] = None
    doc_type: Optional[str] = None


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
        source_meta = []
        if chunk.doc_type:
            source_meta.append(chunk.doc_type)
        if chunk.job_id:
            source_meta.append(f"job={chunk.job_id}")
        if chunk.created_at:
            source_meta.append(chunk.created_at[:10])
        
        meta_str = f" ({', '.join(source_meta)})" if source_meta else ""
        header = f"--- [{chunk.source}{meta_str}] chunk {chunk.chunk_index + 1} ---"
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
    """Indexes project artifacts and blueprints for RAG retrieval with explicit chunking."""

    MANIFEST_NAME = "rag_index_manifest.json"

    def __init__(
        self,
        workspace_path: Path,
        project_id: str,
        *,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
        index_dir: Optional[Path] = None,
        scope: Optional[MemoryScope] = None,
    ):
        self.workspace_path = Path(workspace_path)
        self.project_id = project_id
        self.scope = scope
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.index: Optional[VectorStoreIndex] = None
        
        if index_dir:
            self.index_path = Path(index_dir)
        else:
            self.index_path = self.workspace_path / f"index_{project_id}"
            
        self._splitter = SentenceSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        self._indexed_sources: List[str] = []

        _init_embeddings()
        self._try_load_persisted_index()

    def _try_load_persisted_index(self) -> None:
        if not self.index_path.is_dir() or not (self.index_path / "docstore.json").is_file():
            return
        try:
            storage = StorageContext.from_defaults(persist_dir=str(self.index_path))
            self.index = load_index_from_storage(storage)
            manifest = self.index_path / self.MANIFEST_NAME if (self.index_path / self.MANIFEST_NAME).is_file() else (self.workspace_path / self.MANIFEST_NAME)
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
            if self.scope:
                manifest["org_id"] = self.scope.org_id
                manifest["domain"] = self.scope.domain
            
            (self.index_path / self.MANIFEST_NAME).write_text(
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
        auto_persist: bool = True,
    ) -> int:
        """Index raw text under *source* label; returns number of chunks inserted."""
        if not text or not text.strip():
            return 0
        metadata: Dict[str, Any] = {
            "file_path": source,
            "project_id": self.project_id,
            "doc_type": doc_type,
            "source": source,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if self.scope:
            metadata["org_id"] = self.scope.org_id
            metadata["framework"] = self.scope.project_id
            metadata["domain"] = self.scope.domain

        if extra_metadata:
            metadata.update(extra_metadata)
            
        doc = Document(text=text, metadata=metadata)
        count = self._insert_documents([doc])
        if source not in self._indexed_sources:
            self._indexed_sources.append(source)
        logger.debug("Indexed %d chunk(s) from source %r", count, source)
        if auto_persist:
            self._persist_index()
        return count

    def index_file_at_path(
        self,
        file_path: Union[str, Path],
        *,
        source_label: Optional[str] = None,
        doc_type: str = "reference",
        extra_metadata: Optional[Dict[str, Any]] = None,
        auto_persist: bool = True,
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
        meta = {"file_type": path.suffix, "absolute_path": str(path.resolve())}
        if extra_metadata:
            meta.update(extra_metadata)
            
        return self.index_text(
            content,
            label,
            doc_type=doc_type,
            extra_metadata=meta,
            auto_persist=auto_persist,
        )

    def index_artifacts(self, artifact_files: List[str]) -> None:
        """Index project artifacts (relative paths under workspace)."""
        total = 0
        for file_path in artifact_files:
            full_path = self.workspace_path / file_path
            if not full_path.exists():
                logger.warning("Artifact file not found: %s", file_path)
                continue
            total += self.index_file_at_path(full_path, source_label=file_path, doc_type="artifact", auto_persist=False)
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
                created_at = meta.get("created_at")
                job_id = meta.get("job_id")
                doc_type = meta.get("doc_type")
                
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
                        created_at=created_at,
                        job_id=job_id,
                        doc_type=doc_type,
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


def _capture_code_graph(workspace_path: Path) -> Optional[str]:
    """Capture code_graph.json via call graph edges after warming tldr cache."""
    code_graph_file = workspace_path / "code_graph.json"
    if code_graph_file.exists():
        try:
            content = code_graph_file.read_text(encoding="utf-8", errors="replace").strip()
            if content:
                try:
                    parsed = json.loads(content)
                    if isinstance(parsed, dict) and (parsed.get("edges") or parsed.get("workspace_files")):
                        return content
                except Exception:
                    return content
        except OSError:
            pass

    from ..tools.tldr_tools import refresh_call_graph, read_call_graph, _resolve_tldr_bin

    tldr_available = bool(_resolve_tldr_bin())
    if tldr_available:
        try:
            refresh_call_graph(workspace_path)
            edges = read_call_graph(workspace_path)
            if edges:
                content = json.dumps({"edges": edges}, indent=2)
                try:
                    code_graph_file.write_text(content, encoding="utf-8")
                except OSError:
                    pass
                return content
            else:
                # tldr is available but produced 0 edges — do not store empty blueprint
                return None
        except Exception as e:
            logger.debug("Could not refresh/read call graph for code graph: %s", e)
            return None

    # Fallback: scan source tree layout when tldr is unavailable
    from .vendor_paths import prune_dirnames

    file_list = []
    for root, dirnames, files in os.walk(workspace_path):
        # Prune during the walk rather than filtering paths afterwards. The old
        # check was `str(rel).startswith(("node_modules", "target", "build"))`,
        # which only caught vendored code sitting at the workspace root —
        # frontend/node_modules/react/index.js passed straight through, and the
        # walk descended into it either way.
        prune_dirnames(dirnames)
        for f in files:
            rel = Path(root, f).relative_to(workspace_path)
            if not str(rel).startswith((".", "index_")):
                file_list.append(str(rel))

    if file_list:
        summary = {"workspace_files": sorted(file_list[:100])}
        content = json.dumps(summary, indent=2)
        try:
            code_graph_file.write_text(content, encoding="utf-8")
        except OSError:
            pass
        return content

    return None


def index_approved_solution(
    scope: MemoryScope,
    workspace_path: Path,
    job_id: str,
    *,
    score: Optional[int] = None,
) -> int:
    """
    Persist approved solution artifacts (solution_spec.md, wiring_contract.json,
    stack_manifest.json, code_graph.json) to the Postgres crew_context database.
    """
    workspace_path = Path(workspace_path)
    if not workspace_path.exists():
        return 0

    from ..memory.postgres_context_store import PostgresContextStore, sync_job_from_sqlite

    store = PostgresContextStore()

    # Read artifacts
    json_artifacts: Dict[str, Any] = {}
    json_files = [
        ("wiring_contract.json", "wiring_contract"),
        ("stack_manifest.json", "stack_manifest"),
        ("creation_manifest.json", "creation_manifest"),
        ("api_contract.json", "api_contract"),
    ]
    for filename, doc_type in json_files:
        file_path = workspace_path / filename
        if file_path.is_file():
            try:
                json_artifacts[doc_type] = json.loads(file_path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning("Could not parse %s for job %s: %s", filename, job_id, e)

    prose_documents: List[Dict[str, str]] = []
    sol_spec = workspace_path / "solution_spec.md"
    if sol_spec.is_file():
        try:
            prose_documents.append({
                "doc_type": "solution_spec",
                "text": sol_spec.read_text(encoding="utf-8", errors="replace"),
            })
        except OSError:
            pass

    # Read call graph edges
    edges: List[Dict[str, str]] = []
    graph_text = _capture_code_graph(workspace_path)
    if graph_text:
        try:
            parsed = json.loads(graph_text)
            if isinstance(parsed, dict) and "edges" in parsed:
                edges = parsed["edges"]
        except Exception:
            pass

    # Sourced job vision & status
    job_db_env = os.getenv("JOB_DB_PATH", "/app/data/crew_jobs.db")
    sync_job_from_sqlite(job_db_env, job_id, scope.org_id, scope.project_id, scope.domain, store=store)

    success = store.record_job(
        job_id=job_id,
        scope_org=scope.org_id,
        scope_project=scope.project_id,
        scope_domain=scope.domain,
        vision="",  # sync_job_from_sqlite populates vision if present
        json_artifacts=json_artifacts,
        prose_documents=prose_documents,
        call_graph_edges=edges,
    )

    if success:
        logger.info("Persisted solution artifacts for job %s to Postgres crew_context scope %s", job_id, scope.describe())
        return len(json_artifacts) + len(prose_documents)

    # Recall is allowed to go quiet when the plane is unreachable — a write is
    # not. A silent 0 here means this job's blueprint is simply lost, and the
    # next job re-derives an architecture that already existed.
    logger.error(
        "Context plane write FAILED for job %s (scope %s): %d artifact(s) and %d "
        "document(s) were not persisted. Check CREW_DOC_INDEX_DSN and that the "
        "crew_context database is reachable.",
        job_id, scope.describe(), len(json_artifacts), len(prose_documents),
    )
    return 0


def recall_scoped_blueprints(
    scope: MemoryScope,
    query_text: str,
    *,
    top_k: int = DEFAULT_RAG_TOP_K,
    max_chars: int = DEFAULT_MAX_RAG_CONTEXT_CHARS,
) -> List[RetrievedChunk]:
    """
    Recall blueprint chunks from Postgres crew_context database.
    Filter out jobs that failed validation. Fail open and log loudly if DB is unreachable.
    """
    from ..memory.postgres_context_store import PostgresContextStore

    store = PostgresContextStore()
    passed_job_ids = store.get_passed_jobs_in_scope(
        org_id=scope.org_id,
        project_id=scope.project_id,
        domain=scope.domain,
    )

    if not passed_job_ids:
        logger.info("No validated prior jobs found in Postgres crew_context for scope %s", scope.describe())
        return []

    chunks: List[RetrievedChunk] = []
    seen: set[str] = set()

    for jid in passed_job_ids:
        for doc_type, label in [("solution_spec", "solution_spec.md"), ("wiring_contract", "wiring_contract.json"), ("stack_manifest", "stack_manifest.json")]:
            artifact = store.get_artifact(jid, doc_type)
            if not artifact:
                continue

            text_content = json.dumps(artifact, indent=2) if isinstance(artifact, (dict, list)) else str(artifact)
            if not text_content.strip():
                continue

            chunk_key = f"{jid}:{doc_type}"
            if chunk_key not in seen:
                seen.add(chunk_key)
                chunks.append(RetrievedChunk(
                    text=text_content.strip(),
                    source=label,
                    chunk_index=0,
                    score=1.0,
                    job_id=jid,
                    doc_type=doc_type,
                ))

    # Respect total max_chars budget
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

