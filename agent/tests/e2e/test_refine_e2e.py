"""
E2E tests for refinement flow: create completed job, POST refine, assert phase transitions.
Mark as e2e and slow when using real LLM.
"""
import json
import sys
import time
from pathlib import Path

import pytest

# Add paths
root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "agent"))
sys.path.insert(0, str(root / "agent" / "src"))


@pytest.mark.e2e
@pytest.mark.slow
def test_refine_phase_transitions():
    """Create a completed job, POST /refine, poll until phase leaves 'refining'."""
    from crew_studio.llamaindex_web_app import app, job_db
    app.config["TESTING"] = True
    with app.test_client() as client:
        # Create job and mark completed (skip running real workflow)
        r = client.post("/api/jobs", json={"vision": "Simple counter app"})
        assert r.status_code == 201
        job_id = json.loads(r.data)["job_id"]
        job_db.update_job(job_id, {"status": "completed", "current_phase": "completed"})
        # Start refinement
        r2 = client.post(
            f"/api/jobs/{job_id}/refine",
            json={"prompt": "Add a comment at the top of the main file."},
            content_type="application/json",
        )
        assert r2.status_code == 202
        # Poll until no longer refining (or timeout)
        deadline = time.time() + 120
        while time.time() < deadline:
            r3 = client.get(f"/api/jobs/{job_id}/progress")
            assert r3.status_code == 200
            data = json.loads(r3.data)
            if data.get("current_phase") != "refining":
                assert data["current_phase"] in ("completed", "refinement_failed")
                break
            time.sleep(2)
        else:
            pytest.fail("Refinement did not finish within 120s")
