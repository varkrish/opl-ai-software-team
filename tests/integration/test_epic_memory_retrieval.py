"""
Integration smoke test: verifies that epic_memory indexes call graph deltas into
DocumentIndexer and that subsequent RAG queries actually retrieve prior story context.

Does NOT require an LLM — uses local HuggingFace embeddings (BAAI/bge-small-en-v1.5)
or falls back gracefully if the embeddings model is unavailable.

Run with:
    pytest tests/integration/test_epic_memory_retrieval.py -v
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


def _write_call_graph(workspace: Path, edges: list[dict]) -> None:
    cache_dir = workspace / ".tldr" / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "call_graph.json").write_text(
        json.dumps({"edges": edges, "languages": ["python"], "timestamp": 1.0}),
        encoding="utf-8",
    )


def _edge(from_file, from_func, to_file, to_func):
    return {"from_file": from_file, "from_func": from_func,
            "to_file": to_file, "to_func": to_func}


@pytest.mark.integration
def test_prior_story_calls_retrievable_from_rag(tmp_path):
    """
    After indexing story 1's call graph delta, a dev-phase RAG query for
    'implement serializer for User model' should surface story 1's edges.
    """
    from llamaindex_crew.utils.document_indexer import DocumentIndexer
    from llamaindex_crew.utils.epic_memory import index_story_memory
    from llamaindex_crew.utils.rag_context import get_phase_rag_context

    # Story 1 introduces User model and auth call chain
    story1_edges = [
        _edge("views.py", "login_view",       "models.py",      "User.authenticate"),
        _edge("views.py", "login_view",       "utils.py",       "generate_token"),
        _edge("models.py", "User.authenticate", "db.py",         "query_user_by_email"),
        _edge("serializers.py", "UserSerializer", "models.py",  "User"),
    ]
    _write_call_graph(tmp_path, story1_edges)

    indexer = DocumentIndexer(tmp_path, "integration-test-job")

    with patch("llamaindex_crew.utils.epic_memory.refresh_call_graph"):
        index_story_memory(
            tmp_path, indexer,
            story_key="PROJ-1",
            story_index=0,
            story_summary="Implement user login, registration, and JWT auth",
        )

    if not indexer.has_index:
        pytest.skip("DocumentIndexer has no index — embeddings model unavailable in this env")

    # Story 2 dev agent writes serializers.py — query should find story 1's User edges
    rag_ctx = get_phase_rag_context(
        indexer,
        "development",
        extra_query="Implement serializers.py. Serialize User model for API responses.",
    )

    assert rag_ctx, "RAG context is empty — prior story was not indexed"
    assert "PROJ-1" in rag_ctx, "Story key not found in RAG context"
    assert "User" in rag_ctx or "serializer" in rag_ctx.lower(), \
        "Expected User-related content in RAG context"


@pytest.mark.integration
def test_delta_grows_across_stories(tmp_path):
    """
    Each story's call edges appear in the index only once.
    Story 2 edges don't duplicate story 1 edges.
    """
    from llamaindex_crew.utils.document_indexer import DocumentIndexer
    from llamaindex_crew.utils.epic_memory import index_story_memory

    story1_edges = [
        _edge("models.py", "User.__init__",   "db.py",    "connect"),
        _edge("views.py",  "login_view",      "models.py", "User"),
    ]
    story2_edges = story1_edges + [
        _edge("catalog.py", "ProductView",    "models.py", "Product"),
        _edge("catalog.py", "list_products",  "db.py",     "query_products"),
    ]

    # Story 1
    _write_call_graph(tmp_path, story1_edges)
    indexer = DocumentIndexer(tmp_path, "delta-test-job")
    with patch("llamaindex_crew.utils.epic_memory.refresh_call_graph"):
        index_story_memory(tmp_path, indexer, "PROJ-1", 0, "Implement user model")

    sources_after_story1 = indexer.source_count

    # Story 2 adds 2 new edges
    _write_call_graph(tmp_path, story2_edges)
    with patch("llamaindex_crew.utils.epic_memory.refresh_call_graph"):
        index_story_memory(tmp_path, indexer, "PROJ-2", 1, "Implement product catalog")

    sources_after_story2 = indexer.source_count

    # story:PROJ-1:calls + story:PROJ-1:progress + story:PROJ-2:calls + story:PROJ-2:progress
    assert sources_after_story2 > sources_after_story1, \
        "Story 2 should add new sources to the index"
    assert sources_after_story2 <= 4, \
        f"Expected at most 4 sources (2 per story), got {sources_after_story2}"


@pytest.mark.integration
def test_progress_note_retrievable_without_call_graph(tmp_path):
    """
    Even with no .tldr cache, the progress note alone is indexed and retrievable.
    """
    from llamaindex_crew.utils.document_indexer import DocumentIndexer
    from llamaindex_crew.utils.epic_memory import index_story_memory

    indexer = DocumentIndexer(tmp_path, "no-cache-test-job")

    # No .tldr/cache at all
    with patch("llamaindex_crew.utils.epic_memory.refresh_call_graph"):
        index_story_memory(
            tmp_path, indexer,
            story_key="PROJ-5",
            story_index=4,
            story_summary="Implement invoice generation with PDF export",
        )

    if not indexer.has_index:
        pytest.skip("Embeddings unavailable")

    results = indexer.query("invoice PDF generation completed story", top_k=3)
    assert any("PROJ-5" in r or "invoice" in r.lower() for r in results), \
        "Progress note for PROJ-5 not found in RAG index"


@pytest.mark.integration
def test_epic_progress_md_human_readable(tmp_path):
    """
    epic_progress.md is written and contains human-readable story summaries
    and call edge samples — useful for debugging agent decisions.
    """
    from llamaindex_crew.utils.document_indexer import DocumentIndexer
    from llamaindex_crew.utils.epic_memory import index_story_memory

    edges = [
        _edge("auth.py", "login", "models.py", "User.check_password"),
        _edge("auth.py", "logout", "sessions.py", "invalidate_session"),
    ]
    _write_call_graph(tmp_path, edges)
    indexer = DocumentIndexer(tmp_path, "progress-md-test")

    with patch("llamaindex_crew.utils.epic_memory.refresh_call_graph"):
        index_story_memory(
            tmp_path, indexer,
            story_key="PROJ-3",
            story_index=2,
            story_summary="Implement login and logout with session management",
        )

    md_path = tmp_path / "epic_progress.md"
    assert md_path.exists(), "epic_progress.md was not created"

    content = md_path.read_text(encoding="utf-8")
    assert "PROJ-3" in content
    assert "login and logout" in content.lower() or "session" in content.lower()
    assert "auth.py::login" in content
    assert "models.py::User.check_password" in content
