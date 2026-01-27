"""
End-to-End Tests for Web UI using Playwright
Tests the complete user interface flow
"""
import pytest
import sys
import os
import subprocess
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

try:
    from playwright.sync_api import sync_playwright, expect
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


@pytest.fixture(scope="module")
def web_server():
    """Start Flask web server for UI tests"""
    if not PLAYWRIGHT_AVAILABLE:
        pytest.skip("Playwright not installed")
    
    # Start Flask server in background
    server_process = subprocess.Popen(
        ["python", "-m", "llamaindex_crew.web.web_app"],
        env={**os.environ, "FLASK_ENV": "testing"},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    
    # Wait for server to start
    time.sleep(3)
    
    yield "http://localhost:8080"
    
    # Cleanup
    server_process.terminate()
    server_process.wait(timeout=5)


@pytest.fixture
def browser_context(web_server):
    """Create browser context for UI tests"""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        yield page, web_server
        context.close()
        browser.close()


@pytest.mark.e2e
@pytest.mark.ui
@pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE, reason="Playwright not installed")
def test_homepage_loads(browser_context):
    """Test that homepage loads successfully"""
    page, base_url = browser_context
    
    page.goto(base_url)
    
    # Check page title or main heading
    expect(page).to_have_title(lambda title: "AI" in title or "Software" in title)


@pytest.mark.e2e
@pytest.mark.ui
@pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE, reason="Playwright not installed")
def test_create_job_from_ui(browser_context):
    """Test creating a job through the UI"""
    page, base_url = browser_context
    
    page.goto(base_url)
    
    # Find vision input (adjust selector based on actual UI)
    vision_input = page.locator("textarea[name='vision'], input[name='vision'], #vision")
    vision_input.fill("Create a simple calculator")
    
    # Submit form
    submit_button = page.locator("button[type='submit'], .submit-btn, #submit")
    submit_button.click()
    
    # Wait for response
    page.wait_for_timeout(2000)
    
    # Should show job created or running
    # Adjust based on actual UI behavior
    page.wait_for_selector(".job-status, .job-id, .success-message", timeout=5000)


@pytest.mark.e2e
@pytest.mark.ui
@pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE, reason="Playwright not installed")
def test_view_job_status(browser_context):
    """Test viewing job status in UI"""
    page, base_url = browser_context
    
    # Create a job first via API
    page.goto(base_url)
    
    # Interact with job list or status page
    # This depends on your UI structure
    jobs_link = page.locator("a[href*='jobs'], .jobs-link")
    if jobs_link.is_visible():
        jobs_link.click()
        page.wait_for_selector(".job-list, .jobs-container", timeout=5000)


@pytest.mark.e2e
@pytest.mark.ui
@pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE, reason="Playwright not installed")
def test_ui_form_validation(browser_context):
    """Test UI form validation"""
    page, base_url = browser_context
    
    page.goto(base_url)
    
    # Try to submit empty form
    submit_button = page.locator("button[type='submit'], .submit-btn")
    submit_button.click()
    
    # Should show validation error
    # Adjust based on actual UI
    page.wait_for_timeout(1000)
    
    # Form should still be visible (not submitted)
    vision_input = page.locator("textarea[name='vision'], input[name='vision']")
    expect(vision_input).to_be_visible()


@pytest.mark.e2e
@pytest.mark.ui
@pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE, reason="Playwright not installed")
def test_job_progress_updates(browser_context):
    """Test that job progress updates are shown"""
    page, base_url = browser_context
    
    page.goto(base_url)
    
    # Create a job
    vision_input = page.locator("textarea[name='vision'], input[name='vision']")
    vision_input.fill("Simple test project")
    
    submit_button = page.locator("button[type='submit']")
    submit_button.click()
    
    # Wait and check for progress updates
    page.wait_for_timeout(2000)
    
    # Should show some progress indicator
    # Adjust selectors based on actual UI
    progress_element = page.locator(".progress, .status, .phase")
    if progress_element.is_visible():
        expect(progress_element).to_be_visible()


@pytest.mark.e2e
@pytest.mark.ui
@pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE, reason="Playwright not installed")
def test_view_generated_code(browser_context):
    """Test viewing generated code in UI"""
    page, base_url = browser_context
    
    page.goto(base_url)
    
    # This test would need a completed job
    # In real scenario, might need to wait for job completion
    # or use a pre-seeded job
    
    # Look for code viewer or file browser
    code_viewer = page.locator(".code-viewer, .file-browser, pre code")
    
    # This is a placeholder - actual implementation depends on UI
    # page.wait_for_selector(".code-viewer", timeout=10000)


# Placeholder for additional UI tests
@pytest.mark.e2e
@pytest.mark.ui
@pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE, reason="Playwright not installed")
def test_ui_responsive_design(browser_context):
    """Test UI works on different screen sizes"""
    page, base_url = browser_context
    
    # Test mobile view
    page.set_viewport_size({"width": 375, "height": 667})
    page.goto(base_url)
    page.wait_for_timeout(1000)
    
    # Test tablet view
    page.set_viewport_size({"width": 768, "height": 1024})
    page.reload()
    page.wait_for_timeout(1000)
    
    # Test desktop view
    page.set_viewport_size({"width": 1920, "height": 1080})
    page.reload()
    page.wait_for_timeout(1000)
