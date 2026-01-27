"""
End-to-end test for LlamaIndex workflow with a simple calculator use case
"""
import sys
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from llamaindex_crew.main import run_workflow


def test_calculator_e2e():
    """Test LlamaIndex workflow with a simple calculator vision"""
    vision = "Create a simple Python calculator with add and subtract functions. The calculator should have a Calculator class with add(a, b) and subtract(a, b) methods. Include unit tests using pytest."
    
    # Use test workspace
    workspace_path = Path(__file__).parent / "test_workspace_llamaindex_e2e"
    workspace_path.mkdir(exist_ok=True)
    
    print("=" * 80)
    print("🧪 E2E Test: Simple Calculator with LlamaIndex Workflow")
    print("=" * 80)
    print(f"Vision: {vision}")
    print(f"Workspace: {workspace_path}")
    print(f"OpenRouter Key Present: {bool(os.getenv('OPENROUTER_API_KEY'))}")
    print("=" * 80)
    print()
    
    try:
        results = run_workflow(
            vision=vision,
            project_id="test_calculator_e2e",
            workspace_path=str(workspace_path)
        )
        
        print("\n" + "=" * 80)
        print("✅ Workflow completed!")
        print("=" * 80)
        print(f"Status: {results['status']}")
        print(f"State: {results['state']}")
        
        # Print budget report
        budget = results.get('budget_report', {})
        print(f"\n💰 Budget Report:")
        print(f"  Total Cost: ${budget.get('total_cost', 0):.4f}")
        print(f"  Budget Used: {budget.get('budget_used_pct', 0):.1f}%")
        
        # Verify key files exist
        required_files = [
            "user_stories.md",
            "design_spec.md",
            "tech_stack.md"
        ]
        
        print("\n📁 Checking generated files...")
        for file_name in required_files:
            file_path = workspace_path / file_name
            if file_path.exists():
                print(f"  ✅ {file_name}")
            else:
                print(f"  ❌ {file_name} (missing)")
        
        # Check for source files
        src_dir = workspace_path / "src"
        if src_dir.exists():
            src_files = list(src_dir.rglob("*.py"))
            print(f"\n📝 Found {len(src_files)} Python files in src/")
            for src_file in src_files[:10]:  # Show first 10
                print(f"  - {src_file.relative_to(workspace_path)}")
        
        # Check for test files
        test_files = list(workspace_path.rglob("test_*.py")) + list(workspace_path.rglob("*_test.py"))
        if test_files:
            print(f"\n🧪 Found {len(test_files)} test files:")
            for test_file in test_files[:5]:
                print(f"  - {test_file.relative_to(workspace_path)}")
        
        # Task validation
        task_validation = results.get('task_validation', {})
        if task_validation.get('valid'):
            print("\n✅ All tasks completed successfully")
        else:
            print("\n⚠️  Some tasks incomplete:")
            incomplete = task_validation.get('incomplete_tasks', [])
            failed = task_validation.get('failed_tasks', [])
            if incomplete:
                print(f"  Incomplete: {len(incomplete)}")
            if failed:
                print(f"  Failed: {len(failed)}")
        
        print(f"\n📁 All output files in: {workspace_path}")
        print("=" * 80)
        
        return results
        
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        raise


if __name__ == "__main__":
    test_calculator_e2e()
