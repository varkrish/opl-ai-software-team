#!/usr/bin/env python3
"""
One-off script to run the DevOps agent with a real LLM and verify it writes
Containerfile and Tekton pipeline. Requires LLM config (e.g. ~/.crew-ai/config.yaml).
Usage: from repo root:
  export PYTHONPATH=agent:agent/src
  python agent/scripts/run_devops_agent_real.py
Or from agent dir:
  export PYTHONPATH=.:src
  python scripts/run_devops_agent_real.py
"""
import sys
from pathlib import Path

# Ensure agent src is on path
root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "src"))

def main():
    workspace_path = root / "workspace" / "devops_test_job"
    workspace_path.mkdir(parents=True, exist_ok=True)
    project_id = "devops-test-job"
    tech_stack = "Python 3.11, FastAPI. Backend only, no frontend. Use uvicorn to run."

    print("DevOps agent real run")
    print("  workspace:", workspace_path)
    print("  tech_stack:", tech_stack)
    print("  pipeline_type: tekton (default)")
    print()

    from src.llamaindex_crew.agents.devops_agent import DevOpsAgent

    agent = DevOpsAgent(workspace_path, project_id)
    print("Running agent (this may take a minute with a real LLM)...")
    result = agent.run(tech_stack=tech_stack, pipeline_type="tekton")
    print("Run completed.")
    print("Agent response (last 500 chars):", result[-500:] if len(result) > 500 else result)
    print()

    # Check for expected artifacts
    containerfile = workspace_path / "Containerfile"
    containerfile_alt = workspace_path / "Dockerfile"
    tekton_dir = workspace_path / ".tekton"
    all_files = list(workspace_path.rglob("*"))
    created = [f.relative_to(workspace_path) for f in all_files if f.is_file()]

    print("Files under workspace:")
    for p in sorted(created):
        print(" ", p)
    if containerfile.exists():
        print("  -> Containerfile found")
    elif containerfile_alt.exists():
        print("  -> Dockerfile found (acceptable)")
    else:
        print("  -> No Containerfile/Dockerfile found")
    if tekton_dir.exists() and any(tekton_dir.iterdir()):
        print("  -> .tekton/ has files")
    else:
        print("  -> .tekton/ missing or empty")

if __name__ == "__main__":
    main()
