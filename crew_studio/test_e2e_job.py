import sys
import os
import time
from pathlib import Path

# Fix python path for local imports
sys.path.insert(0, "/app/agent/src")
sys.path.insert(0, "/app/agent")
sys.path.insert(0, "/app")

from src.llamaindex_crew.config import ConfigLoader

# Force JOB_DB_PATH before importing web app so they share the same DB
os.environ["JOB_DB_PATH"] = "/app/crew_jobs_test.db"

from crew_studio.llamaindex_web_app import run_job_async, job_db

def main():
    db_path = Path("/app/crew_jobs_test.db")
    # If we want a clean slate, drop the tables using the existing job_db connection
    with job_db._get_conn() as conn:
        conn.execute("DELETE FROM jobs")
        conn.execute("DELETE FROM refinements")
        
    config = ConfigLoader.load()

    
    print("Testing End-To-End Job Placement...")
    
    vision = """
    Create a Frappe app called 'library_management'.
    Feature 1: Create a DocType called 'Article' with fields: title (Data), author (Data), status (Select: Available, Issued).
    Feature 2: Add a controller hook in article.py `before_insert` to automatically set the status of a new Article to 'Available'.
    """
    
    # 1. Create Job in DB (just like UI does)
    import uuid
    job_id = "test-job-" + str(uuid.uuid4())[:8]
    workspace_path = f"/app/workspace/{job_id}"
    
    job_db.create_job(
        job_id=job_id,
        vision=vision,
        workspace_path=workspace_path,
        owner_id="test-user",
        team_id="test-team"
    )
    
    print(f"✅ Job Created: {job_id}")
    
    # 2. Trigger async runner
    print("🚀 Triggering async build runner...")
    thread = run_job_async(job_id, vision, job_config=config, resume=False)
    
    # 3. Poll DB until job completes or fails
    print("⏳ Waiting for job to complete (this will take several minutes)...")
    while True:
        job = job_db.get_job(job_id)
        status = job.get("status")
        phase = job.get("current_phase")
        progress = job.get("progress")
        
        print(f"Status: {status} | Phase: {phase} | Progress: {progress}%")
        
        if status in ("completed", "failed", "partially_completed"):
            if status == "failed":
                print(f"❌ Job Failed: {job.get('error')}")
            else:
                print(f"✅ Job Finished: {status}")
            break
            
        time.sleep(10)

if __name__ == "__main__":
    main()
