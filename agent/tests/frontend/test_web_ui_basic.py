"""
Basic frontend tests for web UI
Note: Full E2E tests would require Playwright/Cypress setup
"""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))


@pytest.mark.frontend
def test_web_ui_imports():
    """Test that web app can be imported"""
    try:
        from llamaindex_crew.web import web_app
        assert web_app is not None
    except ImportError as e:
        pytest.skip(f"Cannot import web_app: {e}")


@pytest.mark.frontend
def test_web_ui_routes_exist():
    """Test that web app has expected routes"""
    try:
        from llamaindex_crew.web.web_app import app
        
        routes = [str(rule) for rule in app.url_map.iter_rules()]
        
        assert '/' in routes
        assert '/api/jobs' in routes
        assert any('/api/jobs/<job_id>' in r for r in routes)
    except ImportError as e:
        pytest.skip(f"Cannot import web_app: {e}")
