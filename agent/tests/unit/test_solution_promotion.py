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

        storage_root = tmp_path / "storage"
        monkeypatch.setenv("CREW_DOCUMENT_INDEX_DIR", str(storage_root))

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

        storage_root = tmp_path / "storage"
        monkeypatch.setenv("CREW_DOCUMENT_INDEX_DIR", str(storage_root))

        scope = MemoryScope(org_id="user1", project_id="spring-boot", domain="tasks")

        # Index an approved solution into persistent storage
        (ws / "solution_spec.md").write_text("# Approved Spec\nTask API endpoints", encoding="utf-8")
        index_approved_solution(scope, ws, job_id="job_001", base_dir=storage_root)

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

