import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, "/app/agent/src")
sys.path.insert(0, "/app/crew_studio")

from artifact_assertions import assert_or_exit, validate_tech_stack  # noqa: E402
from llamaindex_crew.agents.tech_architect_agent import TechArchitectAgent  # noqa: E402


def main():
    test_workspace = Path("/app/workspace/job-test-architect-123")
    if test_workspace.exists():
        shutil.rmtree(test_workspace)
    test_workspace.mkdir(parents=True, exist_ok=True)

    print("Testing Tech Architect Agent isolated...")
    agent = TechArchitectAgent(workspace_path=test_workspace)

    vision = (
        "Create a Frappe App for Movie Ticket Management. "
        "Include payment using stripe and reuse erpnext modules."
    )
    design_spec = (
        "Modules: Ticketing. DocTypes: Screen, Seat, Showtime, Booking, Payment. "
        "Integrate Stripe. Webhooks in hooks.py."
    )

    agent.define_tech_stack(design_spec, vision, context_digest="No existing context")

    print("\n\n=== FILES CREATED IN WORKSPACE ===")
    for path in sorted(p for p in test_workspace.rglob("*") if p.is_file()):
        print(path)

    print("\n\n=== CONTENT ASSERTIONS ===")
    assert_or_exit(validate_tech_stack(test_workspace), "Tech Architect")

    tech_stack = (test_workspace / "tech_stack.md").read_text(encoding="utf-8")
    print("\n\n=== TECH STACK (preview) ===")
    print(tech_stack[:1500])
    if len(tech_stack) > 1500:
        print(f"\n... ({len(tech_stack) - 1500} more chars)")

    print("\n\nTEST PASSED: Tech Architect artifacts valid")


if __name__ == "__main__":
    main()
