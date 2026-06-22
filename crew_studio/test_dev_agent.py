import sys
import os
from pathlib import Path

# Fix python path for local imports
sys.path.insert(0, "/app/agent/src")

from llamaindex_crew.agents.dev_agent import DevAgent

def main():
    test_workspace = Path("/app/workspace/job-test-architect-123")
    
    # We do NOT wipe the workspace because we want to use the tech_stack.md 
    # created by the tech architect in the previous test.
    if not test_workspace.exists():
        print("Workspace does not exist. Run test_tech_architect.py first.")
        sys.exit(1)
        
    tech_stack_path = test_workspace / "tech_stack.md"
    if not tech_stack_path.exists():
        print("tech_stack.md does not exist in workspace.")
        sys.exit(1)
        
    tech_stack_content = tech_stack_path.read_text()
    
    print("Testing Dev Agent isolated...")
    agent = DevAgent(workspace_path=test_workspace)
    
    features = [
        "Create the Frappe app structure (if it doesn't exist)",
        "Create the Screen DocType",
        "Create the Seat DocType"
    ]
    
    result = agent.implement_features(features, tech_stack_content, user_stories="")
    
    print("\n\n=== DEV AGENT RESULT ===")
    print(result)
    
    print("\n\n=== FILES CREATED IN WORKSPACE ===")
    os.system(f"find {test_workspace} -type f")

if __name__ == "__main__":
    main()
