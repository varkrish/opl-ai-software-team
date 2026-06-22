import sys
import os
from pathlib import Path

# Fix python path for local imports
sys.path.insert(0, "/app/agent/src")

from llamaindex_crew.agents.refinement_agent import RefinementAgent
from llamaindex_crew.budget.tracker import EnhancedBudgetTracker

def main():
    test_workspace = Path("/app/workspace/job-test-refactor-123")
    
    # Setup dummy workspace
    if test_workspace.exists():
        os.system(f"rm -rf {test_workspace}")
    test_workspace.mkdir(parents=True, exist_ok=True)
    
    dummy_file_path = test_workspace / "booking.py"
    dummy_content = """class BookingController:
    def __init__(self):
        self.base_price = 100
        self.currency = "USD"
        
    def calculate_price(self):
        return self.base_price

    def confirm_booking(self):
        print("Booking confirmed!")
"""
    # Pad file to simulate a real-world size
    for i in range(150):
        dummy_content += f"    # padding comment {i}\n"
    
    dummy_file_path.write_text(dummy_content)
    
    print("Testing Refinement Agent with read-then-patch workflow...")
    agent = RefinementAgent(workspace_path=test_workspace, project_id="job-test-refactor-123", budget_tracker=EnhancedBudgetTracker())
    
    user_prompt = "Refactor the BookingController. Modify calculate_price to accept an optional discount parameter and return base_price - discount."
    
    result = agent.run(
        user_prompt=user_prompt,
        file_path="booking.py",
        initial_file_content=dummy_content,
        scope="file"
    )
    
    print("\n\n=== REFINEMENT AGENT RESULT ===")
    print(result)
    
    print("\n\n=== FILE CONTENTS (tail) ===")
    os.system(f"tail -n 10 {dummy_file_path}")
    
    # Assertions
    new_content = dummy_file_path.read_text()
    if "discount" in new_content and "padding comment 149" in new_content:
        print("\n✅ SUCCESS: Agent patched the file without deleting the rest of the code!")
    else:
        print("\n❌ FAILURE: Agent either failed to add the discount feature or deleted the rest of the file!")

if __name__ == "__main__":
    main()
