"""Unit tests for persistent scope-partitioned DocumentIndexer and blueprint RAG (Fix 4)."""
import json
from pathlib import Path
from llamaindex_crew.memory.scope import MemoryScope
from llamaindex_crew.utils.document_indexer import (
    DocumentIndexer,
    RetrievedChunk,
    format_retrieved_chunks,
    index_approved_solution,
    recall_scoped_blueprints,
)


class TestPersistentDocumentIndexer:
    def test_the_per_job_index_lives_in_the_workspace(self, tmp_path):
        """
        Cross-job persistence moved to Postgres; DocumentIndexer.for_scope and
        the ~/.crew/doc_index tree are gone. What remains is the per-job index,
        which lives beside the job it serves and dies with it — product_owner
        and refinement_context query it within a run.
        """
        indexer = DocumentIndexer(tmp_path, "job_123")

        assert indexer.index_path == tmp_path / "index_job_123"
        assert not hasattr(DocumentIndexer, "for_scope"), (
            "for_scope wrote a second copy of every artifact to ~/.crew that "
            "nothing reads any more"
        )

    def test_index_text_attaches_scope_metadata(self, tmp_path):
        scope = MemoryScope(org_id="team_acme", project_id="spring-boot", domain="finance")
        indexer = DocumentIndexer(tmp_path, "spring-boot", scope=scope)
        
        count = indexer.index_text(
            "Spring Boot Finance API specification with OpenAPI contract.",
            source="solution_spec.md",
            doc_type="solution_spec",
            extra_metadata={"job_id": "job_123"},
        )
        assert count > 0
        assert indexer.has_index
        assert indexer.source_count == 1

        chunks = indexer.retrieve("Finance API specification")
        assert len(chunks) > 0
        assert isinstance(chunks[0], RetrievedChunk)
        assert chunks[0].source == "solution_spec.md"
        assert chunks[0].job_id == "job_123"
        assert chunks[0].doc_type == "solution_spec"

    def test_index_approved_solution_persists_to_the_context_store(self, tmp_path, monkeypatch):
        """
        Artifacts now go to the Postgres context plane, not a local index. The
        store is faked here because the assertion worth making is *what is
        handed to it* — a real DB round-trip belongs in an integration test.
        """
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "solution_spec.md").write_text("# Solution Spec\nSpring Boot task service architecture.", encoding="utf-8")
        (ws / "wiring_contract.json").write_text(json.dumps({"package": "com.example.task"}), encoding="utf-8")
        (ws / "stack_manifest.json").write_text(json.dumps({"chosen_stack": ["Spring Boot 3", "PostgreSQL"]}), encoding="utf-8")

        captured = {}

        class _FakeStore:
            def record_job(self, **kwargs):
                captured.update(kwargs)
                return True

        from llamaindex_crew.memory import postgres_context_store as pcs
        monkeypatch.setattr(pcs, "PostgresContextStore", lambda *a, **k: _FakeStore())
        monkeypatch.setattr(pcs, "sync_job_from_sqlite", lambda *a, **k: None)

        scope = MemoryScope(org_id="org_1", project_id="spring-boot", domain="task-mgmt")
        count = index_approved_solution(scope, ws, job_id="b13dde92", score=9)

        assert count >= 3, "spec, contract and manifest should all be persisted"
        assert captured["job_id"] == "b13dde92"
        assert captured["scope_org"] == "org_1"
        assert "wiring_contract" in captured["json_artifacts"]
        assert any(d["doc_type"] == "solution_spec" for d in captured["prose_documents"])

    def test_a_failed_context_store_write_is_loud(self, tmp_path, monkeypatch, caplog):
        """
        Recall may go quiet when the plane is unreachable; a write may not. A
        silent 0 means this job's blueprint is lost and the next job re-derives
        an architecture that already existed.
        """
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "solution_spec.md").write_text("# Spec", encoding="utf-8")

        class _DeadStore:
            def record_job(self, **kwargs):
                return False

        from llamaindex_crew.memory import postgres_context_store as pcs
        monkeypatch.setattr(pcs, "PostgresContextStore", lambda *a, **k: _DeadStore())
        monkeypatch.setattr(pcs, "sync_job_from_sqlite", lambda *a, **k: None)

        scope = MemoryScope(org_id="org_1", project_id="p", domain="d")
        with caplog.at_level("ERROR"):
            count = index_approved_solution(scope, ws, job_id="j1")

        assert count == 0
        assert any("context plane write failed" in r.message.lower() for r in caplog.records), (
            f"an unreachable plane must be reported, got: {[r.message for r in caplog.records]}"
        )

    def test_format_retrieved_chunks_truncation(self):
        chunks = [
            RetrievedChunk(
                text="Content for chunk 1 " * 50,
                source="spec.md",
                chunk_index=0,
                job_id="job1",
                doc_type="solution_spec",
                created_at="2026-08-09T10:00:00Z",
            ),
            RetrievedChunk(
                text="Content for chunk 2 " * 50,
                source="wiring.json",
                chunk_index=1,
                job_id="job1",
                doc_type="wiring_contract",
                created_at="2026-08-09T10:00:00Z",
            ),
        ]
        formatted = format_retrieved_chunks(chunks, max_chars=300)
        assert "spec.md" in formatted
        assert "(retrieval budget reached)" in formatted or len(formatted) <= 400

    def test_fail_open_on_nonexistent_workspace(self, tmp_path):
        scope = MemoryScope(org_id="org_1", project_id="spring-boot", domain="task-mgmt")
        non_existent = tmp_path / "does_not_exist"
        count = index_approved_solution(scope, non_existent, job_id="job_999")
        assert count == 0
