"""Unit tests for approved solution promotion and blueprint recall injection (Fix 5)."""
import json
from unittest.mock import MagicMock, patch
from pathlib import Path
from llamaindex_crew.memory.scope import MemoryScope
from llamaindex_crew.memory.recall import recall_solution_blueprints, recall_solutioning_context
from llamaindex_crew.utils.document_indexer import index_approved_solution
from crew_studio.memory_hooks import write_approved_solution_memory


class TestSolutionPromotionAndRecall:
    def test_write_approved_solution_memory_hook(self, tmp_path, monkeypatch):
        ws = tmp_path / "job_workspace"
        ws.mkdir()
        (ws / "solution_spec.md").write_text("# Spring Boot Architecture\nUse JPA with Postgres", encoding="utf-8")
        (ws / "wiring_contract.json").write_text(json.dumps({"services": ["TaskService"]}), encoding="utf-8")

        captured = {}

        class _FakeStore:
            """Round-trips one job in memory, so write-then-recall is testable
            without a live Postgres. Outcomes are recorded as passing because
            this test is about the write/recall path, not the outcome filter —
            that is covered in test_blueprint_outcome_filter."""

            def record_job(self, **kwargs):
                captured.update(kwargs)
                return True

            def get_passed_jobs_in_scope(self, org_id, project_id, domain, required_checks=None):
                return [captured["job_id"]] if captured else []

            def get_artifact(self, job_id, doc_type):
                return (captured.get("json_artifacts") or {}).get(doc_type)

            def get_prose_documents(self, job_id, doc_type=None):
                return captured.get("prose_documents") or []

        from llamaindex_crew.memory import postgres_context_store as pcs
        monkeypatch.setattr(pcs, "PostgresContextStore", lambda *a, **k: _FakeStore())
        monkeypatch.setattr(pcs, "sync_job_from_sqlite", lambda *a, **k: None)

        mock_config = MagicMock()
        mock_config.memory.enabled = True

        count = write_approved_solution_memory(
            "job_abc123",
            config=mock_config,
            job={"owner_id": "user1", "metadata": {"framework": "spring-boot", "domain": "tasks"}},
            workspace_path=ws,
            score=9,
        )
        assert count >= 2
        assert captured["job_id"] == "job_abc123"
        assert "wiring_contract" in captured["json_artifacts"]

        # Verify recall
        recalled = recall_solution_blueprints(
            mock_config,
            vision="Build Spring Boot task service",
            job={"owner_id": "user1", "metadata": {"framework": "spring-boot", "domain": "tasks"}},
            workspace_path=ws,
        )
        assert "REUSED BLUEPRINTS" in recalled
        assert "solution_spec.md" in recalled or "TaskService" in recalled

    def test_recall_solutioning_context_combines_memmachine_and_blueprints(self, tmp_path, monkeypatch):
        ws = tmp_path / "ws"
        ws.mkdir()

        # Blueprints come from the Postgres context plane; stub the retrieval so
        # this test covers the recall *composition*, not the store.
        from llamaindex_crew.utils import document_indexer as di
        monkeypatch.setattr(
            di, "recall_scoped_blueprints",
            lambda scope, query, **kw: [
                di.RetrievedChunk(
                    text="# Approved Spec\nTask API endpoints",
                    source="solution_spec.md", chunk_index=0,
                    job_id="job_001", doc_type="solution_spec",
                )
            ],
        )

        mock_config = MagicMock()
        mock_config.memory.enabled = True
        mock_config.memory.read_at_solutioning = True

        with patch("llamaindex_crew.memory.recall.build_recall_query", return_value="query"):
            context = recall_solutioning_context(
                mock_config,
                vision="Build task API",
                job={"owner_id": "user1", "metadata": {"framework": "spring-boot", "domain": "tasks"}},
                workspace_path=ws,
            )
            assert "REUSED BLUEPRINTS" in context

