import os
import sys
import unittest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add src and asgi_app directory to Python Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "agent" / "src"))

import pytest
from fastapi.testclient import TestClient

from crew_studio.job_database import JobDatabase

class TestBYOKDatabase(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = Path(self.test_dir) / "test_jobs.db"
        self.db = JobDatabase(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_schema_user_llm_configs_created(self):
        """Verify the user_llm_configs table exists on DB initialization."""
        with self.db._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='user_llm_configs'"
            )
            row = cursor.fetchone()
            self.assertIsNotNone(row)

    def test_save_get_delete_llm_config(self):
        """Test encryption, decryption, retrieval, and deletion of user LLM config."""
        owner_id = "user-123"
        api_base_url = "https://custom.api.com/v1"
        api_key = "sk-secret-key-xyz"
        model_manager = "gpt-4o"
        model_worker = "gpt-4"
        model_reviewer = "gpt-3.5-turbo"

        # Initially should be None
        self.assertIsNone(self.db.get_llm_config(owner_id))

        # Save config
        self.db.save_llm_config(
            owner_id=owner_id,
            api_base_url=api_base_url,
            api_key=api_key,
            model_manager=model_manager,
            model_worker=model_worker,
            model_reviewer=model_reviewer,
        )

        # Get config & verify decryption
        config = self.db.get_llm_config(owner_id)
        self.assertIsNotNone(config)
        self.assertEqual(config["api_base_url"], api_base_url)
        self.assertEqual(config["api_key"], api_key)
        self.assertEqual(config["model_manager"], model_manager)
        self.assertEqual(config["model_worker"], model_worker)
        self.assertEqual(config["model_reviewer"], model_reviewer)
        self.assertIn("updated_at", config)

        # Check that it's encrypted in the DB
        with self.db._get_conn() as conn:
            row = conn.execute(
                "SELECT encrypted_key FROM user_llm_configs WHERE owner_id = ?",
                (owner_id,)
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertNotEqual(row["encrypted_key"], api_key)

        # Delete config
        deleted = self.db.delete_llm_config(owner_id)
        self.assertTrue(deleted)
        self.assertIsNone(self.db.get_llm_config(owner_id))


class TestBYOKThreadLocalAndContext(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = Path(self.test_dir) / "test_jobs.db"
        self.db = JobDatabase(self.db_path)

        # Mock global server config loading
        from src.llamaindex_crew.config.secure_config import SecretConfig, LLMConfig
        self.mock_fallback = SecretConfig(
            llm=LLMConfig(
                api_key="server-default-key",
                api_base_url="https://server.openai.com/v1",
                model_manager="server-manager",
                model_worker="server-worker",
                model_reviewer="server-reviewer"
            )
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_thread_local_propagation(self):
        """Test thread-local context sets, retrieves, and clears LLM configuration."""
        from src.llamaindex_crew.utils.llm_config import (
            set_thread_config,
            clear_thread_config,
            get_llm_for_agent,
            _thread_config
        )

        # Clear active config if any
        clear_thread_config()

        # Check default config is loaded
        with patch("src.llamaindex_crew.config.ConfigLoader.load", return_value=self.mock_fallback):
            llm = get_llm_for_agent("worker")
            self.assertEqual(llm.api_key, "server-default-key")

        # Set thread config
        from copy import deepcopy
        custom_cfg = deepcopy(self.mock_fallback)
        custom_cfg.llm.api_key = "custom-key"
        set_thread_config(custom_cfg)

        llm = get_llm_for_agent("worker")
        self.assertEqual(llm.api_key, "custom-key")

        # Clear and check fallback
        clear_thread_config()
        with patch("src.llamaindex_crew.config.ConfigLoader.load", return_value=self.mock_fallback):
            llm = get_llm_for_agent("worker")
            self.assertEqual(llm.api_key, "server-default-key")

    def test_user_llm_context_manager(self):
        """Verify user_llm_context resolves configuration dynamically."""
        from src.llamaindex_crew.utils.llm_config import user_llm_context, get_llm_for_agent

        owner_id = "user-abc"
        self.db.create_job(
            job_id="job-abc",
            vision="Test",
            workspace_path="/tmp/null",
            owner_id=owner_id
        )

        # Save user LLM config
        self.db.save_llm_config(
            owner_id=owner_id,
            api_base_url="https://user.url/v1",
            api_key="user-token",
            model_manager="user-manager",
            model_worker="user-worker",
            model_reviewer="user-reviewer"
        )

        # Use context manager
        with user_llm_context("job-abc", self.db, self.mock_fallback) as active_config:
            self.assertEqual(active_config.llm.api_key, "user-token")
            self.assertEqual(active_config.llm.model_worker, "user-worker")
            
            # Verify LLM instantiated in context picks up user config
            llm = get_llm_for_agent("worker")
            self.assertEqual(llm.api_key, "user-token")
            self.assertEqual(llm.api_base, "https://user.url/v1")

        # Outside context, should fall back
        with patch("src.llamaindex_crew.config.ConfigLoader.load", return_value=self.mock_fallback):
            llm = get_llm_for_agent("worker")
            self.assertEqual(llm.api_key, "server-default-key")


class TestBYOKApiEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Override AUTH_ENABLED to False or mock get_current_user to run requests without real Keycloak auth
        from crew_studio import auth
        auth.AUTH_ENABLED = False
        auth.MOCK_USER.user_id = "mock_user_id"

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = Path(self.test_dir) / "test_jobs.db"
        self.db = JobDatabase(self.db_path)

        # Patch asgi_app to use our test DB
        self.db_patcher = patch("crew_studio.asgi_app.job_db", self.db)
        self.db_patcher.start()

        from crew_studio.asgi_app import app
        self.client = TestClient(app)

    def tearDown(self):
        self.db_patcher.stop()
        shutil.rmtree(self.test_dir)

    def test_api_crud_flow(self):
        """Verify GET, POST, DELETE routes for LLM configuration."""
        # 1. GET - Initially not configured
        resp = self.client.get("/api/llm/config")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["configured"])

        # 2. POST - Save configuration
        payload = {
            "api_base_url": "https://foo.bar/v1",
            "api_key": "sk-test-token-1234",
            "model_manager": "gpt-4o",
            "model_worker": "gpt-4",
            "model_reviewer": "gpt-3.5-turbo"
        }
        resp = self.client.post("/api/llm/config", json=payload)
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(resp.json()["saved"])

        # 3. GET - Verify config returned (token masked)
        resp = self.client.get("/api/llm/config")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["configured"])
        self.assertEqual(data["api_base_url"], "https://foo.bar/v1")
        self.assertEqual(data["api_token_masked"], "************1234")

        # 4. POST - Rotation support (submitting masked token doesn't overwrite)
        payload["api_key"] = "************1234"
        payload["api_base_url"] = "https://new.bar/v1"
        resp = self.client.post("/api/llm/config", json=payload)
        self.assertEqual(resp.status_code, 201)

        config = self.db.get_llm_config("mock_user_id") # MOCK_USER has user_id="mock_user_id"
        self.assertEqual(config["api_base_url"], "https://new.bar/v1")
        self.assertEqual(config["api_key"], "sk-test-token-1234") # Preserved!

        # 5. DELETE - Remove configuration
        resp = self.client.delete("/api/llm/config")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["deleted"])

        resp = self.client.get("/api/llm/config")
        self.assertFalse(resp.json()["configured"])

    def test_api_post_validation(self):
        """Test schema validation on base URL."""
        payload = {
            "api_base_url": "invalid-url-schema",
            "api_key": "token"
        }
        resp = self.client.post("/api/llm/config", json=payload)
        self.assertEqual(resp.status_code, 422)

    @patch("src.llamaindex_crew.utils.llm_config.GenericLlamaLLM")
    def test_api_test_connection(self, mock_llm_class):
        """Verify API test-connection route checks key validity."""
        mock_llm_instance = MagicMock()
        mock_llm_class.return_value = mock_llm_instance

        # Test success
        mock_llm_instance.complete.return_value = MagicMock(text="OK")
        payload = {
            "api_base_url": "https://test.com/v1",
            "api_key": "some-token"
        }
        resp = self.client.post("/api/llm/test-connection", json=payload)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["ok"])

        # Test failure
        mock_llm_instance.complete.side_effect = Exception("Auth failed")
        resp = self.client.post("/api/llm/test-connection", json=payload)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["ok"])
        self.assertIn("Auth failed", resp.json()["error"])

    @patch("src.llamaindex_crew.utils.llm_config.GenericLlamaLLM")
    def test_api_test_connection_multiple_models(self, mock_llm_class):
        """Verify API test-connection validates all three models individually."""
        mock_llm_instance = MagicMock()
        mock_llm_class.return_value = mock_llm_instance

        # Success case for all models
        mock_llm_instance.complete.return_value = MagicMock(text="OK")
        payload = {
            "api_base_url": "https://test.com/v1",
            "api_key": "some-token",
            "model_manager": "model-1",
            "model_worker": "model-2",
            "model_reviewer": "model-3",
        }
        resp = self.client.post("/api/llm/test-connection", json=payload)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["ok"])

        # Mocking individual model failure (say worker fails, others pass)
        mock_llm_instance.complete.side_effect = [
            MagicMock(text="OK"),  # manager
            Exception("Model 2 not found"),  # worker
            MagicMock(text="OK")   # reviewer
        ]
        resp = self.client.post("/api/llm/test-connection", json=payload)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["ok"])
        self.assertIn("Worker Model 'model-2': Model 2 not found", resp.json()["error"])


class TestModelContextWindows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Initialize an in-memory SQLite DB or temporary file DB
        cls.db_path = Path("test_context_windows.db")
        cls.db = JobDatabase(cls.db_path)

    @classmethod
    def tearDownClass(cls):
        if cls.db_path.exists():
            cls.db_path.unlink()
            # Clean up WAL files if they exist
            for ext in ["-wal", "-shm"]:
                p = Path(f"{cls.db_path}{ext}")
                if p.exists():
                    p.unlink()

    def test_prepopulated_defaults(self):
        """Verify model_context_windows has default values prepopulated."""
        gpt4_ctx = self.db.get_model_context_window("gpt-4o-mini")
        self.assertEqual(gpt4_ctx, 128000)

        claude_ctx = self.db.get_model_context_window("claude-3-5-sonnet")
        self.assertEqual(claude_ctx, 200000)

        unknown_ctx = self.db.get_model_context_window("unknown-model")
        self.assertIsNone(unknown_ctx)

    def test_longest_match_prioritization(self):
        """Verify lookup uses the longest (most specific) pattern match."""
        # 'gpt-4' -> 8192, 'gpt-4o' -> 128000
        # 'gpt-4o-mini' matches both, but 'gpt-4o' is longer
        ctx = self.db.get_model_context_window("gpt-4o-mini")
        self.assertEqual(ctx, 128000)

    def test_crud_operations(self):
        """Verify we can save and delete custom context windows."""
        pattern = "my-custom-llm"
        self.db.save_model_context_window(pattern, 64000)
        
        ctx = self.db.get_model_context_window("my-custom-llm-v2")
        self.assertEqual(ctx, 64000)

        # Delete it
        deleted = self.db.delete_model_context_window(pattern)
        self.assertTrue(deleted)

        ctx_after = self.db.get_model_context_window("my-custom-llm-v2")
        self.assertIsNone(ctx_after)
