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
    def test_for_scope_creates_partitioned_directory(self, tmp_path):
        scope = MemoryScope(org_id="team_acme", project_id="spring-boot", domain="finance")
        indexer = DocumentIndexer.for_scope(scope, base_dir=tmp_path)
        
        expected_dir = tmp_path / "team-acme" / "spring-boot" / "finance"
        assert indexer.index_path == expected_dir
        assert expected_dir.is_dir()

    def test_index_text_attaches_scope_metadata(self, tmp_path):
        scope = MemoryScope(org_id="team_acme", project_id="spring-boot", domain="finance")
        indexer = DocumentIndexer.for_scope(scope, base_dir=tmp_path)
        
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

    def test_index_approved_solution(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        
        (ws / "solution_spec.md").write_text("# Solution Spec\nSpring Boot task service architecture.", encoding="utf-8")
        (ws / "wiring_contract.json").write_text(json.dumps({"package": "com.example.task"}), encoding="utf-8")
        (ws / "stack_manifest.json").write_text(json.dumps({"chosen_stack": ["Spring Boot 3", "PostgreSQL"]}), encoding="utf-8")

        storage_root = tmp_path / "storage"
        scope = MemoryScope(org_id="org_1", project_id="spring-boot", domain="task-mgmt")

        count = index_approved_solution(scope, ws, job_id="b13dde92", score=9, base_dir=storage_root)
        assert count >= 3

        recalled = recall_scoped_blueprints(scope, "task service architecture", base_dir=storage_root)
        assert len(recalled) > 0
        sources = {c.source for c in recalled}
        assert "solution_spec.md" in sources or "wiring_contract.json" in sources

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
        count = index_approved_solution(scope, non_existent, job_id="job_999", base_dir=tmp_path / "storage")
        assert count == 0
