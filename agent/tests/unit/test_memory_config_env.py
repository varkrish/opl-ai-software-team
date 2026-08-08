"""
Tests for MemoryConfig defaults and the environment overrides that let the
compose 'memory' profile toggle the plane without editing config.yaml.

The default must be OFF: an operator who upgrades and does nothing should see no
behaviour change and no attempted connections.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_AGENT_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(_AGENT_SRC))

from llamaindex_crew.config.secure_config import (  # noqa: E402
    ConfigLoader,
    MemoryConfig,
    SecretConfig,
)


class TestMemoryConfigDefaults(unittest.TestCase):
    def test_disabled_by_default(self):
        self.assertFalse(MemoryConfig().enabled)

    def test_safe_defaults(self):
        config = MemoryConfig()
        self.assertIsNone(config.base_url)
        self.assertEqual(config.default_org_id, "default")
        self.assertEqual(config.shared_project_id, "shared-context")
        self.assertEqual(config.summary_agent_type, "reviewer")
        self.assertTrue(config.write_job_outcome)
        self.assertTrue(config.read_at_solutioning)

    def test_present_on_secret_config_without_being_specified(self):
        config = SecretConfig(llm={"api_key": "k"})
        self.assertIsInstance(config.memory, MemoryConfig)
        self.assertFalse(config.memory.enabled)


class TestMemoryEnvOverrides(unittest.TestCase):
    def _apply(self, env, config_data=None):
        data = config_data if config_data is not None else {}
        with patch.dict(os.environ, env, clear=False):
            # Clear any inherited values not under test.
            for key in (
                "MEMORY_ENABLED", "MEMMACHINE_BASE_URL", "MEMORY_BACKEND_URL",
                "MEMMACHINE_API_KEY", "MEMORY_SEARCH_LIMIT", "MEMORY_TIMEOUT_SECONDS",
                "MEMORY_MAX_RECALL_CHARS", "MEMORY_SUMMARY_AGENT_TYPE",
                "MEMORY_SHARED_PROJECT_ID", "MEMORY_DEFAULT_ORG_ID",
            ):
                if key not in env:
                    os.environ.pop(key, None)
            ConfigLoader._apply_memory_env_overrides(data)
        return data

    def test_memory_enabled_truthy_forms(self):
        for value in ("true", "TRUE", "1", "yes", "on"):
            data = self._apply({"MEMORY_ENABLED": value})
            self.assertTrue(data["memory"]["enabled"], f"failed for {value!r}")

    def test_memory_enabled_falsy_forms(self):
        for value in ("false", "0", "no", "off", ""):
            data = self._apply({"MEMORY_ENABLED": value})
            self.assertFalse(data["memory"]["enabled"], f"failed for {value!r}")

    def test_unset_env_leaves_file_value_intact(self):
        # An unset variable must never clobber config.yaml.
        data = self._apply({}, {"memory": {"enabled": True, "search_limit": 9}})
        self.assertTrue(data["memory"]["enabled"])
        self.assertEqual(data["memory"]["search_limit"], 9)

    def test_env_wins_over_file(self):
        data = self._apply({"MEMORY_ENABLED": "false"}, {"memory": {"enabled": True}})
        self.assertFalse(data["memory"]["enabled"])

    def test_base_url_from_either_variable(self):
        self.assertEqual(
            self._apply({"MEMMACHINE_BASE_URL": "http://a:8080"})["memory"]["base_url"],
            "http://a:8080",
        )
        # MEMORY_BACKEND_URL is the name the upstream client itself reads.
        self.assertEqual(
            self._apply({"MEMORY_BACKEND_URL": "http://b:8080"})["memory"]["base_url"],
            "http://b:8080",
        )

    def test_numeric_overrides_are_cast(self):
        data = self._apply(
            {"MEMORY_SEARCH_LIMIT": "12", "MEMORY_MAX_RECALL_CHARS": "999"}
        )
        self.assertEqual(data["memory"]["search_limit"], 12)
        self.assertEqual(data["memory"]["max_recall_chars"], 999)

    def test_invalid_numeric_override_is_ignored_not_fatal(self):
        data = self._apply({"MEMORY_SEARCH_LIMIT": "not-a-number"})
        self.assertNotIn("search_limit", data.get("memory", {}))

    def test_overrides_produce_a_valid_model(self):
        data = self._apply(
            {"MEMORY_ENABLED": "true", "MEMMACHINE_BASE_URL": "http://m:8080"}
        )
        config = SecretConfig(llm={"api_key": "k"}, **data)
        self.assertTrue(config.memory.enabled)
        self.assertEqual(config.memory.base_url, "http://m:8080")

    def test_no_memory_key_added_when_nothing_set(self):
        data = self._apply({})
        self.assertNotIn("memory", data)


if __name__ == "__main__":
    unittest.main()
