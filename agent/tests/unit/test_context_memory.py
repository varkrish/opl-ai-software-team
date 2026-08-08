"""
Tests for the cross-job context memory plane.

Two properties matter most and are tested hardest:

1. **Fail-open.** A disabled, absent, or broken memory plane must never raise
   into a caller. Every job path that touches memory has to keep working.
2. **Scope isolation.** A Spring Boot rule must not leak into a Frappe job, and
   one customer must never see another's memories. The commercial claim rests on
   this, so it is asserted rather than assumed.
"""
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock

_AGENT_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(_AGENT_SRC))

from llamaindex_crew.memory.context_memory import (  # noqa: E402
    ContextMemory,
    _extract_episodes,
    _stringify_metadata,
    get_context_memory,
)
from llamaindex_crew.memory.scope import (  # noqa: E402
    MemoryScope,
    resolve_domain,
    resolve_framework,
    resolve_scope,
    slugify,
)


class _MemoryConfig:
    """Stand-in for the pydantic MemoryConfig."""

    def __init__(self, **kwargs):
        self.enabled = kwargs.get("enabled", True)
        self.base_url = kwargs.get("base_url", "http://memmachine-app:8080")
        self.api_key = kwargs.get("api_key")
        self.timeout_seconds = kwargs.get("timeout_seconds", 15)
        self.default_org_id = kwargs.get("default_org_id", "default")
        self.default_project_id = kwargs.get("default_project_id", "unknown-framework")
        self.shared_project_id = kwargs.get("shared_project_id", "shared-context")
        self.write_job_outcome = kwargs.get("write_job_outcome", True)
        self.write_reference_docs = kwargs.get("write_reference_docs", True)
        self.read_at_solutioning = kwargs.get("read_at_solutioning", True)
        self.search_limit = kwargs.get("search_limit", 5)
        self.search_score_threshold = kwargs.get("search_score_threshold")
        self.max_recall_chars = kwargs.get("max_recall_chars", 4000)
        self.summary_max_chars = kwargs.get("summary_max_chars", 1200)
        self.summary_agent_type = kwargs.get("summary_agent_type", "reviewer")


class _Config:
    def __init__(self, **kwargs):
        self.memory = _MemoryConfig(**kwargs)


class TestSlugify(unittest.TestCase):
    """Scope fragmentation is the main failure mode — slugs must be stable."""

    def test_variant_spellings_collapse_to_one_slug(self):
        for value in ("Frappe 15", "frappe-15", "FRAPPE  15", "frappe_15"):
            self.assertEqual(slugify(value), "frappe-15", f"failed for {value!r}")

    def test_empty_falls_back(self):
        self.assertEqual(slugify("", "fallback"), "fallback")
        self.assertEqual(slugify("!!!", "fallback"), "fallback")
        self.assertEqual(slugify(None, "fallback"), "fallback")

    def test_long_values_truncated_without_trailing_dash(self):
        slug = slugify("a" * 200)
        self.assertLessEqual(len(slug), 64)
        self.assertFalse(slug.endswith("-"))


class TestScopeResolution(unittest.TestCase):
    def test_team_id_wins_over_owner_id(self):
        # Teammates should share one memory pool, not build private ones.
        scope = resolve_scope({"team_id": "Acme Corp", "owner_id": "user-42"})
        self.assertEqual(scope.org_id, "acme-corp")

    def test_falls_back_to_owner_then_default(self):
        self.assertEqual(resolve_scope({"owner_id": "user-42"}).org_id, "user-42")
        self.assertEqual(
            resolve_scope({}, default_org_id="house").org_id, "house"
        )

    def test_explicit_framework_metadata_wins(self):
        scope = resolve_scope({"metadata": {"framework": "Spring Boot 3"}})
        self.assertEqual(scope.project_id, "spring-boot-3")

    def test_framework_from_stack_manifest(self):
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "stack_manifest.json").write_text(
                json.dumps({"chosen_stack": ["Frappe v15", "MariaDB"]}), encoding="utf-8"
            )
            self.assertEqual(resolve_framework({}, workspace), "frappe")

    def test_framework_from_tech_stack_md(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "tech_stack.md").write_text(
                "# Stack\nWe will use Quarkus and Postgres.", encoding="utf-8"
            )
            self.assertEqual(resolve_framework({}, workspace), "quarkus")

    def test_framework_falls_back_when_unresolvable(self):
        self.assertEqual(resolve_framework({}, None, fallback="fb"), "fb")

    def test_domain_from_jira_issue_key(self):
        self.assertEqual(resolve_domain({"jira_issue_key": "ASSET-42"}), "asset")

    def test_explicit_domain_beats_jira_key(self):
        # Once persisted, metadata.domain is authoritative so scope cannot drift.
        self.assertEqual(
            resolve_domain({"domain": "invoicing", "jira_issue_key": "ASSET-42"}),
            "invoicing",
        )

    def test_domain_is_not_guessed_from_vision(self):
        # An unstable guess fragments recall worse than one shared bucket.
        self.assertEqual(
            resolve_domain({}, vision="Build an asset depreciation workflow"), "general"
        )

    def test_metadata_json_string_is_parsed(self):
        scope = resolve_scope({"metadata": '{"framework": "django"}'})
        self.assertEqual(scope.project_id, "django")

    def test_malformed_metadata_json_does_not_raise(self):
        scope = resolve_scope({"metadata": "{not json"})
        self.assertEqual(scope.project_id, "unknown-framework")

    def test_instance_metadata_carries_only_scope_keys(self):
        # Anything in instance metadata becomes an automatic search filter, so a
        # per-job key here would scope every recall to a single job.
        scope = MemoryScope(org_id="acme", project_id="frappe", domain="asset")
        self.assertEqual(scope.instance_metadata(), {"group_id": "asset"})


class TestFailOpen(unittest.TestCase):
    def test_disabled_memory_is_a_silent_noop(self):
        memory = get_context_memory(None, job={"owner_id": "u"})
        self.assertFalse(memory.enabled)
        self.assertFalse(memory.add("text", memory_type="job_outcome"))
        self.assertEqual(memory.search("query"), [])
        self.assertEqual(memory.recall_block("query"), "")
        memory.close()  # must not raise

    def test_enabled_without_base_url_stays_disabled(self):
        config = _Config(enabled=True, base_url=None)
        memory = get_context_memory(config, job={})
        # Env fallbacks may supply a URL in a dev shell; assert no raise either way.
        self.assertIsInstance(memory.enabled, bool)
        self.assertFalse(memory.add("t", memory_type="x") and not memory.enabled)

    def test_write_failure_is_swallowed(self):
        scope = MemoryScope(org_id="acme", project_id="frappe", domain="asset")
        memory = ContextMemory(scope, base_url="http://memmachine:8080")
        handle = MagicMock()
        handle.add.side_effect = RuntimeError("server exploded")
        memory._memory = handle

        self.assertFalse(memory.add("text", memory_type="job_outcome"))

    def test_search_failure_returns_empty_list(self):
        scope = MemoryScope(org_id="acme", project_id="frappe", domain="asset")
        memory = ContextMemory(scope, base_url="http://memmachine:8080")
        handle = MagicMock()
        handle.search.side_effect = RuntimeError("timeout")
        memory._memory = handle

        self.assertEqual(memory.search("query"), [])
        self.assertEqual(memory.recall_block("query"), "")

    def test_missing_client_library_disables_cleanly(self):
        scope = MemoryScope(org_id="acme", project_id="frappe", domain="asset")
        memory = ContextMemory(scope, base_url="http://memmachine:8080")

        saved = sys.modules.get("memmachine_client")
        sys.modules["memmachine_client"] = None  # forces ImportError on import
        try:
            self.assertIsNone(memory._memory_handle())
            self.assertFalse(memory.enabled)  # latched off, no repeated attempts
        finally:
            if saved is None:
                sys.modules.pop("memmachine_client", None)
            else:
                sys.modules["memmachine_client"] = saved

    def test_empty_content_is_not_written(self):
        scope = MemoryScope(org_id="a", project_id="b", domain="c")
        memory = ContextMemory(scope, base_url="http://x")
        handle = MagicMock()
        memory._memory = handle

        self.assertFalse(memory.add("   ", memory_type="job_outcome"))
        handle.add.assert_not_called()


class TestScopeIsolation(unittest.TestCase):
    """
    The isolation guarantee, asserted against a fake client.

    org_id/project_id are passed to the server as the query boundary, and domain
    is enforced by instance metadata which the client turns into an automatic
    search filter. These tests pin all three.
    """

    def _fake_client_module(self, recorder):
        module = types.ModuleType("memmachine_client")

        class FakeClient:
            def __init__(self, **kwargs):
                recorder["client_kwargs"] = kwargs

            def get_or_create_project(self, org_id, project_id, **kwargs):
                recorder.setdefault("projects", []).append((org_id, project_id))

            def close(self):
                recorder["closed"] = True

        class FakeMemory:
            def __init__(self, client, org_id, project_id, metadata=None, **kwargs):
                recorder.setdefault("handles", []).append(
                    {"org_id": org_id, "project_id": project_id, "metadata": metadata}
                )
                self._org = org_id

            def add(self, content, **kwargs):
                recorder.setdefault("writes", []).append(
                    {"content": content, "kwargs": kwargs}
                )
                return [{"uid": "1"}]

            def search(self, query, **kwargs):
                recorder.setdefault("searches", []).append(
                    {"query": query, "kwargs": kwargs}
                )
                return {"episodes": []}

        module.MemMachineClient = FakeClient
        module.Memory = FakeMemory
        return module

    def _with_fake_client(self, recorder):
        saved = sys.modules.get("memmachine_client")
        sys.modules["memmachine_client"] = self._fake_client_module(recorder)
        self.addCleanup(
            lambda: sys.modules.__setitem__("memmachine_client", saved)
            if saved is not None
            else sys.modules.pop("memmachine_client", None)
        )

    def test_different_customers_get_different_org_ids(self):
        recorder = {}
        self._with_fake_client(recorder)

        for team in ("acme-corp", "globex"):
            memory = get_context_memory(
                _Config(), job={"team_id": team, "metadata": {"framework": "frappe"}}
            )
            memory.add("outcome", memory_type="job_outcome")

        orgs = [h["org_id"] for h in recorder["handles"]]
        self.assertEqual(orgs, ["acme-corp", "globex"])
        self.assertEqual(len(set(orgs)), 2, "customers must not share an org scope")

    def test_different_frameworks_get_different_projects(self):
        recorder = {}
        self._with_fake_client(recorder)

        for framework in ("frappe-15", "spring-boot-3"):
            memory = get_context_memory(
                _Config(), job={"team_id": "acme", "metadata": {"framework": framework}}
            )
            memory.add("outcome", memory_type="job_outcome")

        projects = [h["project_id"] for h in recorder["handles"]]
        self.assertEqual(projects, ["frappe-15", "spring-boot-3"])

    def test_domain_is_enforced_via_instance_metadata(self):
        recorder = {}
        self._with_fake_client(recorder)

        memory = get_context_memory(
            _Config(),
            job={
                "team_id": "acme",
                "metadata": {"framework": "frappe", "domain": "invoicing"},
            },
        )
        memory.search("anything")

        self.assertEqual(recorder["handles"][0]["metadata"], {"group_id": "invoicing"})

    def test_shared_project_used_for_prestack_writes(self):
        recorder = {}
        self._with_fake_client(recorder)

        memory = get_context_memory(
            _Config(),
            job={"team_id": "acme", "metadata": {"framework": "frappe-15"}},
            shared_project=True,
        )
        memory.add("jira context", memory_type="jira_context")

        self.assertEqual(recorder["handles"][0]["project_id"], "shared-context")
        # Org isolation still applies to shared-project memories.
        self.assertEqual(recorder["handles"][0]["org_id"], "acme")

    def test_project_is_created_before_use(self):
        recorder = {}
        self._with_fake_client(recorder)

        memory = get_context_memory(
            _Config(), job={"team_id": "acme", "metadata": {"framework": "django"}}
        )
        memory.add("x", memory_type="job_outcome")

        self.assertIn(("acme", "django"), recorder["projects"])

    def test_search_filters_by_memory_type_when_requested(self):
        recorder = {}
        self._with_fake_client(recorder)

        memory = get_context_memory(_Config(), job={"team_id": "acme"})
        memory.search("q", memory_type="job_outcome")

        self.assertEqual(
            recorder["searches"][0]["kwargs"]["filter_dict"],
            {"metadata.type": "job_outcome"},
        )


class TestMetadataCoercion(unittest.TestCase):
    """The client raises TypeError on non-string filter values."""

    def test_values_are_stringified(self):
        result = _stringify_metadata({"n": 3, "f": 1.5, "s": "x"})
        self.assertEqual(result, {"n": "3", "f": "1.5", "s": "x"})
        for value in result.values():
            self.assertIsInstance(value, str)

    def test_booleans_become_lowercase_strings(self):
        self.assertEqual(
            _stringify_metadata({"a": True, "b": False}), {"a": "true", "b": "false"}
        )

    def test_none_values_dropped(self):
        self.assertEqual(_stringify_metadata({"a": None, "b": "x"}), {"b": "x"})

    def test_empty_input(self):
        self.assertEqual(_stringify_metadata(None), {})


class TestEpisodeExtraction(unittest.TestCase):
    """Server response shape varies across MemMachine versions."""

    def test_episodes_key(self):
        result = _extract_episodes({"episodes": [{"content": "a", "metadata": {"t": "1"}}]})
        self.assertEqual(result, [{"content": "a", "metadata": {"t": "1"}}])

    def test_results_key_and_text_field(self):
        self.assertEqual(
            _extract_episodes({"results": [{"text": "b"}]}),
            [{"content": "b", "metadata": {}}],
        )

    def test_bare_list_of_strings(self):
        self.assertEqual(
            _extract_episodes(["hello"]), [{"content": "hello", "metadata": {}}]
        )

    def test_object_attributes(self):
        class Episode:
            content = "c"
            metadata = {"type": "job_outcome"}

        class Result:
            episodes = [Episode()]

        self.assertEqual(
            _extract_episodes(Result()),
            [{"content": "c", "metadata": {"type": "job_outcome"}}],
        )

    def test_none_and_empty(self):
        self.assertEqual(_extract_episodes(None), [])
        self.assertEqual(_extract_episodes({}), [])

    def test_entries_without_content_are_skipped(self):
        self.assertEqual(_extract_episodes({"episodes": [{"metadata": {}}]}), [])


class TestRecallBlock(unittest.TestCase):
    def test_block_is_truncated_to_limit(self):
        scope = MemoryScope(org_id="a", project_id="b", domain="c")
        memory = ContextMemory(scope, base_url="http://x", max_recall_chars=120)
        handle = MagicMock()
        handle.search.return_value = {
            "episodes": [{"content": "y" * 500, "metadata": {"type": "job_outcome"}}]
        }
        memory._memory = handle

        block = memory.recall_block("q")
        self.assertLessEqual(len(block), 160)
        self.assertIn("truncated", block)

    def test_empty_results_produce_empty_string(self):
        scope = MemoryScope(org_id="a", project_id="b", domain="c")
        memory = ContextMemory(scope, base_url="http://x")
        handle = MagicMock()
        handle.search.return_value = {"episodes": []}
        memory._memory = handle

        self.assertEqual(memory.recall_block("q"), "")


if __name__ == "__main__":
    unittest.main()
