"""
Unit tests for MigrationAnalysisAgent and MigrationExecutionAgent.
TDD: Written BEFORE implementation — these tests define the prompt contract.
"""
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
import sys

root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "src"))

# Mock heavy LLM dependencies so we only test prompt construction
if "llama_index.llms.ollama" not in sys.modules:
    sys.modules["llama_index.llms.ollama"] = MagicMock()
if "llama_index.embeddings.huggingface" not in sys.modules:
    sys.modules["llama_index.embeddings.huggingface"] = MagicMock()


# ── Helper: skip __init__ to test build_prompt in isolation ──────────────────
def _make_agent(cls):
    """Create an agent instance with __init__ bypassed (no LLM needed)."""
    with patch.object(cls, "__init__", lambda self, *a, **kw: None):
        agent = cls(Path("/tmp/ws"), "job-1")
        agent.workspace_path = Path("/tmp/ws")
        agent.project_id = "job-1"
    return agent


# ═════════════════════════════════════════════════════════════════════════════
# MigrationAnalysisAgent tests
# ═════════════════════════════════════════════════════════════════════════════

class TestMigrationAnalysisAgentPrompt:

    def test_build_prompt_includes_report_path(self):
        from src.llamaindex_crew.agents.migration_agent import MigrationAnalysisAgent
        agent = _make_agent(MigrationAnalysisAgent)
        prompt = agent.build_prompt(
            report_path="docs/mta-report.json",
            migration_goal="Migrate JBoss EAP 7 to 8",
        )
        assert "docs/mta-report.json" in prompt

    def test_build_prompt_includes_migration_goal(self):
        from src.llamaindex_crew.agents.migration_agent import MigrationAnalysisAgent
        agent = _make_agent(MigrationAnalysisAgent)
        prompt = agent.build_prompt(
            report_path="docs/report.csv",
            migration_goal="Spring Boot 2 to 3",
        )
        assert "Spring Boot 2 to 3" in prompt

    def test_build_prompt_includes_file_listing(self):
        from src.llamaindex_crew.agents.migration_agent import MigrationAnalysisAgent
        agent = _make_agent(MigrationAnalysisAgent)
        prompt = agent.build_prompt(
            report_path="docs/report.json",
            migration_goal="Upgrade",
            file_listing="src/App.java\nsrc/Service.java",
        )
        assert "src/App.java" in prompt
        assert "src/Service.java" in prompt

    def test_build_prompt_mentions_migration_plan_json(self):
        """Prompt must instruct agent to write migration_plan.json."""
        from src.llamaindex_crew.agents.migration_agent import MigrationAnalysisAgent
        agent = _make_agent(MigrationAnalysisAgent)
        prompt = agent.build_prompt(
            report_path="docs/report.json",
            migration_goal="Upgrade",
        )
        assert "migration_plan.json" in prompt

    def test_build_prompt_omits_empty_optional_sections(self):
        from src.llamaindex_crew.agents.migration_agent import MigrationAnalysisAgent
        agent = _make_agent(MigrationAnalysisAgent)
        prompt = agent.build_prompt(
            report_path="docs/report.json",
            migration_goal="Upgrade",
            file_listing=None,
            user_notes=None,
        )
        # Should not have empty "## " sections with no content
        assert "## Additional instructions" not in prompt
        assert "## Codebase files" not in prompt or "None" not in prompt


# ═════════════════════════════════════════════════════════════════════════════
# MigrationExecutionAgent tests
# ═════════════════════════════════════════════════════════════════════════════

class TestMigrationExecutionAgentPrompt:

    def test_build_prompt_includes_issue_hints(self):
        from src.llamaindex_crew.agents.migration_agent import MigrationExecutionAgent
        agent = _make_agent(MigrationExecutionAgent)
        issues = [
            {"id": "i-1", "title": "Replace javax", "migration_hint": "javax.inject -> jakarta.inject"},
        ]
        prompt = agent.build_prompt(
            file_path="src/UserService.java",
            file_content="import javax.inject.Inject;",
            issues=issues,
            migration_goal="EAP 7 to 8",
        )
        assert "javax.inject -> jakarta.inject" in prompt
        assert "Replace javax" in prompt

    def test_build_prompt_includes_file_content(self):
        from src.llamaindex_crew.agents.migration_agent import MigrationExecutionAgent
        agent = _make_agent(MigrationExecutionAgent)
        prompt = agent.build_prompt(
            file_path="src/App.java",
            file_content="public class App { }",
            issues=[{"id": "i-1", "title": "t", "migration_hint": "h"}],
            migration_goal="Upgrade",
        )
        assert "public class App { }" in prompt
        assert "src/App.java" in prompt

    def test_build_prompt_includes_repo_rules(self):
        """Tier 2: .migration-rules.md content injected when provided."""
        from src.llamaindex_crew.agents.migration_agent import MigrationExecutionAgent
        agent = _make_agent(MigrationExecutionAgent)
        rules = "Use SLF4J for logging\nKeep JPA 2.x compat"
        prompt = agent.build_prompt(
            file_path="src/App.java",
            file_content="code",
            issues=[{"id": "i-1", "title": "t", "migration_hint": "h"}],
            migration_goal="Upgrade",
            repo_rules=rules,
        )
        assert "SLF4J" in prompt
        assert "JPA 2.x" in prompt

    def test_build_prompt_includes_user_notes(self):
        """Tier 4: per-run migration_notes injected."""
        from src.llamaindex_crew.agents.migration_agent import MigrationExecutionAgent
        agent = _make_agent(MigrationExecutionAgent)
        prompt = agent.build_prompt(
            file_path="src/App.java",
            file_content="code",
            issues=[{"id": "i-1", "title": "t", "migration_hint": "h"}],
            migration_goal="Upgrade",
            user_notes="Skip files under src/auth/",
        )
        assert "Skip files under src/auth/" in prompt

    def test_build_prompt_omits_empty_tiers(self):
        """No empty sections when optional tiers are None."""
        from src.llamaindex_crew.agents.migration_agent import MigrationExecutionAgent
        agent = _make_agent(MigrationExecutionAgent)
        prompt = agent.build_prompt(
            file_path="src/App.java",
            file_content="code",
            issues=[{"id": "i-1", "title": "t", "migration_hint": "h"}],
            migration_goal="Upgrade",
            repo_rules=None,
            user_notes=None,
        )
        assert "## Migration rules" not in prompt
        assert "## Additional instructions" not in prompt

    def test_build_prompt_includes_multiple_issues(self):
        from src.llamaindex_crew.agents.migration_agent import MigrationExecutionAgent
        agent = _make_agent(MigrationExecutionAgent)
        issues = [
            {"id": "i-1", "title": "Replace javax", "migration_hint": "hint 1"},
            {"id": "i-2", "title": "Update XML namespace", "migration_hint": "hint 2"},
            {"id": "i-3", "title": "Remove deprecated API", "migration_hint": "hint 3"},
        ]
        prompt = agent.build_prompt(
            file_path="src/App.java",
            file_content="code",
            issues=issues,
            migration_goal="Upgrade",
        )
        assert "hint 1" in prompt
        assert "hint 2" in prompt
        assert "hint 3" in prompt


class TestMigrationAgentToolBinding:

    def test_analysis_agent_uses_workspace_file_tools(self):
        """MigrationAnalysisAgent tools are bound to workspace_path."""
        from src.llamaindex_crew.agents.migration_agent import MigrationAnalysisAgent
        with patch("src.llamaindex_crew.agents.migration_agent.BaseLlamaIndexAgent") as MockBase:
            MockBase.return_value = MagicMock()
            agent = MigrationAnalysisAgent(Path("/tmp/ws"), "job-1")
            assert MockBase.called
            _, kwargs = MockBase.call_args
            assert "tools" in kwargs
            tool_names = [t.metadata.name for t in kwargs["tools"]]
            assert "file_reader" in tool_names
            assert "file_writer" in tool_names

    def test_execution_agent_uses_workspace_file_tools(self):
        """MigrationExecutionAgent tools are bound to workspace_path."""
        from src.llamaindex_crew.agents.migration_agent import MigrationExecutionAgent
        with patch("src.llamaindex_crew.agents.migration_agent.BaseLlamaIndexAgent") as MockBase:
            MockBase.return_value = MagicMock()
            agent = MigrationExecutionAgent(Path("/tmp/ws"), "job-1")
            assert MockBase.called
            _, kwargs = MockBase.call_args
            assert "tools" in kwargs
            tool_names = [t.metadata.name for t in kwargs["tools"]]
            assert "file_writer" in tool_names
            assert "file_deleter" in tool_names
