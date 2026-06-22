"""Tests for language-agnostic test ↔ source file pairing."""
from llamaindex_crew.utils.test_companion import (
    is_test_file_path,
    resolve_companion_source,
)


def test_is_test_file_path_multi_language():
    assert is_test_file_path("doctype/it_asset/test_it_asset.py")
    assert is_test_file_path("src/utils/foo.test.ts")
    assert is_test_file_path("src/com/example/UserTest.java")
    assert not is_test_file_path("src/main.py")


def test_frappe_colocated_pytest_pairing():
    registered = [
        "it_asset_management/doctype/it_asset/it_asset.py",
        "it_asset_management/doctype/it_asset/it_asset.js",
        "it_asset_management/doctype/it_asset/test_it_asset.py",
    ]
    source = resolve_companion_source(
        "it_asset_management/doctype/it_asset/test_it_asset.py",
        registered,
    )
    assert source == "it_asset_management/doctype/it_asset/it_asset.py"


def test_jest_pairing():
    registered = ["src/utils/parser.ts", "src/utils/parser.test.ts"]
    source = resolve_companion_source("src/utils/parser.test.ts", registered)
    assert source == "src/utils/parser.ts"


def test_junit_pairing_colocated():
    registered = ["com/example/User.java", "com/example/UserTest.java"]
    source = resolve_companion_source("com/example/UserTest.java", registered)
    assert source == "com/example/User.java"


def test_test_does_not_depend_on_js_sibling():
    """Regression: test_it_asset must not match it_asset.js via path substring."""
    from llamaindex_crew.orchestrator.task_manager import TaskDefinition, TaskManager
    from pathlib import Path
    import tempfile

    tech_stack = """
## File Structure
```
it_asset_management/doctype/it_asset/
├── it_asset.json
├── it_asset.py
├── it_asset.js
└── test_it_asset.py
```
"""
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "tasks.db"
        tm = TaskManager(db, "proj")
        tasks = tm.register_granular_tasks("", tech_stack)
        test_task = next(
            t for t in tasks
            if (t.metadata or {}).get("file_path", "").endswith("test_it_asset.py")
        )
        deps = test_task.dependencies or []
        dep_paths = []
        for tid in deps:
            t = tm.get_task_by_id(tid)
            dep_paths.append((t.metadata or {}).get("file_path"))
        assert len(dep_paths) == 1
        assert dep_paths[0].endswith("it_asset.py")
        assert not any(p.endswith(".js") or p.endswith(".json") for p in dep_paths)
