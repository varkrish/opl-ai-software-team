"""
Repeated punctuation is not duplicated code.

``validate_duplicate_code_blocks`` slides a 5-line window over each file and
flags any window that appears twice. It strips blank and comment lines but
keeps everything else, so a window can consist entirely of delimiters.

Live, job 107b3d3e. The check failed on ``tests/frontend_test.js`` for::

    }),
    });
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
    });

Four of those five lines are closing brackets, and the fifth is the default
branch of a fetch mock that every test case in the file shares. That is
ordinary, idiomatic test scaffolding — and repetition in test setup is
deliberate, since tests are kept readable in isolation rather than factored
together.

The cost is not a cosmetic warning. The check fails the job and its findings
feed the remediation loop, so DevAgent is sent to restructure correct test code.
On that job the loop ended ``fix_loop_not_converging: 9 issue(s) remain and no
round improved on the best of 9``.

Two rules, both structural: a window must carry some actual logic to count as a
duplicated block, and test files are not judged for repetition. Genuine
duplication — two copies of a real function body — still has substantive lines
and is still reported.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator  # noqa: E402


def _check(tmp_path):
    return CodeCompletenessValidator.validate_duplicate_code_blocks(tmp_path)


# ── the live false positive ─────────────────────────────────────────────────

FRONTEND_TEST = """\
import App from '../frontend/src/index';

test('renders overview', async () => {
  global.fetch.mockImplementation((url) => {
    if (url.includes('/overview')) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ total: 1 }) });
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
  });
  renderApp();
  await waitFor(() => {
    const el = document.querySelector('span[data-test-id="total-jobs"]');
    if (!el) throw new Error('total jobs element not found');
  });
});

test('renders jobs', async () => {
  global.fetch.mockImplementation((url) => {
    if (url.includes('/jobs')) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ jobs: [] }) });
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
  });
  renderApp();
  await waitFor(() => {
    const el = document.querySelector('span[data-test-id="total-jobs"]');
    if (!el) throw new Error('total jobs element not found');
  });
});
"""


def test_shared_test_scaffolding_is_not_flagged(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "frontend_test.js").write_text(FRONTEND_TEST, encoding="utf-8")

    result = _check(tmp_path)

    assert result["valid"], f"idiomatic test setup was flagged: {result['duplicates']}"


def test_a_window_of_only_delimiters_is_not_a_duplicated_block(tmp_path):
    """Closing brackets carry no logic no matter how often they repeat."""
    src = tmp_path / "app.js"
    src.write_text(
        "function a() {\n"
        "  if (x) {\n    doSomething();\n  }\n}\n});\n});\n}\n});\n}\n"
        "function b() {\n"
        "  if (y) {\n    doOther();\n  }\n}\n});\n});\n}\n});\n}\n",
        encoding="utf-8",
    )

    result = _check(tmp_path)
    flagged = [d["file"] for d in result["duplicates"]]

    assert "app.js" not in flagged, "a bracket run is not duplicated logic"


# ── it must still catch real duplication ────────────────────────────────────

def test_a_genuinely_copied_function_body_is_still_reported(tmp_path):
    body = (
        "    const total = items.reduce((a, b) => a + b.amount, 0);\n"
        "    const average = total / items.length;\n"
        "    const label = formatCurrency(average);\n"
        "    logger.info('computed average', label);\n"
        "    return { total, average, label };\n"
    )
    src = tmp_path / "service.js"
    src.write_text(
        f"function summarise(items) {{\n{body}}}\n\n"
        f"function summariseAgain(items) {{\n{body}}}\n",
        encoding="utf-8",
    )

    result = _check(tmp_path)

    assert not result["valid"], "a copy-pasted function body must still be caught"
    assert result["duplicates"][0]["file"] == "service.js"


def test_duplication_in_production_code_is_still_reported(tmp_path):
    """Only test files get the repetition exemption."""
    body = (
        "    conn = get_connection()\n"
        "    cursor = conn.cursor()\n"
        "    cursor.execute(query, params)\n"
        "    rows = cursor.fetchall()\n"
        "    conn.close()\n"
    )
    (tmp_path / "repo.py").write_text(
        f"def find_users(query, params):\n{body}    return rows\n\n"
        f"def find_orders(query, params):\n{body}    return rows\n",
        encoding="utf-8",
    )

    assert not _check(tmp_path)["valid"]


@pytest.mark.parametrize("path", [
    "tests/test_service.py",
    "test/api_test.go",
    "src/__tests__/App.test.jsx",
    "backend/tests/test_api.py",
    "spec/models_spec.rb",
])
def test_test_files_are_exempt_wherever_they_live(tmp_path, path):
    body = (
        "    conn = get_connection()\n"
        "    cursor = conn.cursor()\n"
        "    cursor.execute(query, params)\n"
        "    rows = cursor.fetchall()\n"
        "    conn.close()\n"
    )
    target = tmp_path / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        f"def one():\n{body}    return rows\n\ndef two():\n{body}    return rows\n",
        encoding="utf-8",
    )

    assert _check(tmp_path)["valid"], f"{path} should be exempt"


def test_a_clean_project_passes(tmp_path):
    (tmp_path / "main.py").write_text(
        "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n",
        encoding="utf-8",
    )
    assert _check(tmp_path)["valid"]


def test_empty_workspace_passes(tmp_path):
    assert _check(tmp_path)["valid"]
