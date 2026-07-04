"""Unit tests for workspace publish filtering."""

from pathlib import Path

from crew_studio.workspace_publish import (
    collect_publishable_files,
    heuristic_github_repo_name,
    should_exclude_from_publish,
)


def test_keeps_planning_docs():
    assert should_exclude_from_publish("user_stories.md", "user_stories.md") is False
    assert should_exclude_from_publish("tech_stack.md", "tech_stack.md") is False
    assert should_exclude_from_publish("solution_spec.md", "solution_spec.md") is False
    assert should_exclude_from_publish("design_spec.md", "design_spec.md") is False


def test_excludes_internal_runtime_files():
    assert should_exclude_from_publish("crew_errors.log", "crew_errors.log") is True
    assert should_exclude_from_publish("delivery_mode_triage.json", "delivery_mode_triage.json") is True
    assert should_exclude_from_publish("agent_backstories.json", "agent_backstories.json") is True
    assert should_exclude_from_publish("agent_prompts.json", "agent_prompts.json") is True
    assert should_exclude_from_publish(f"state_abc.json", "state_abc.json") is True
    assert should_exclude_from_publish("tasks_job.db", "tasks_job.db") is True
    assert should_exclude_from_publish(".tldr/cache/call_graph.json", "call_graph.json") is True


def test_collect_publishable_files(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "user_stories.md").write_text("# stories", encoding="utf-8")
    (ws / "tech_stack.md").write_text("# stack", encoding="utf-8")
    (ws / "main.py").write_text("print('hi')", encoding="utf-8")
    (ws / "crew_errors.log").write_text("err", encoding="utf-8")
    (ws / "state_x.json").write_text("{}", encoding="utf-8")
    tldr = ws / ".tldr" / "cache"
    tldr.mkdir(parents=True)
    (tldr / "call_graph.json").write_text("{}", encoding="utf-8")

    paths = collect_publishable_files(ws)
    assert "user_stories.md" in paths
    assert "tech_stack.md" in paths
    assert "main.py" in paths
    assert "crew_errors.log" not in paths
    assert "state_x.json" not in paths
    assert not any("call_graph" in p for p in paths)


def test_heuristic_repo_name_short():
    name = heuristic_github_repo_name(
        "Build a simple Python CLI tool to track daily habits",
        "69604c6a-2715-4172-89d6-45c8ad94fa74",
    )
    assert name.startswith("crew-ai-")
    assert len(name) <= 40
    assert "build-a-simple-python-cli-tool-to-track" not in name
