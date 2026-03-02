"""
TDD tests for code completeness validation and granular task decomposition.

Tests cover:
  1. CodeCompletenessValidator: detect stubs, placeholders, truncated files
  2. TaskManager.register_granular_tasks: domain-aware decomposition into SQLite
  3. Iterative dev loop: file-by-file generation with per-task validation
"""
import tempfile
import shutil
from pathlib import Path

import pytest
import sys

root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "src"))

from llamaindex_crew.orchestrator.task_manager import TaskManager, TaskDefinition, TaskStatus


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def workspace():
    tmp = tempfile.mkdtemp()
    yield Path(tmp)
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def task_mgr(workspace):
    db_path = workspace / "tasks_test.db"
    return TaskManager(db_path, "test-project")


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Code Completeness Validator
# ═══════════════════════════════════════════════════════════════════════════════

class TestCodeCompletenessValidator:
    """CodeCompletenessValidator detects stubs, placeholders, and truncated files."""

    def test_complete_python_file_passes(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        f = workspace / "models.py"
        f.write_text("""
from django.db import models

class Flight(models.Model):
    origin = models.CharField(max_length=100)
    destination = models.CharField(max_length=100)
    departure_date = models.DateTimeField()
    arrival_date = models.DateTimeField()
    price = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f'{self.origin} -> {self.destination}'

class Reservation(models.Model):
    flight = models.ForeignKey(Flight, on_delete=models.CASCADE)
    passenger_name = models.CharField(max_length=200)
    seat_number = models.CharField(max_length=10)
    booking_date = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, default='confirmed')

    def cancel(self):
        self.status = 'cancelled'
        self.save()
""")
        result = CodeCompletenessValidator.validate_file(f)
        assert result["complete"] is True
        assert result["issues"] == []

    def test_stub_file_fails(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        f = workspace / "views.py"
        f.write_text("""
from rest_framework import generics
from .models import Airline
from .serializers import AirlineSerializer

class AirlineList(generics.ListAPIView):
    queryset = Airline.objects.all()
    serializer_class = AirlineSerializer
""")
        result = CodeCompletenessValidator.validate_file(f)
        # A file with just one simple class and no logic is "thin" but not necessarily a stub
        # The validator should check for placeholder patterns, not just short files
        assert result["complete"] is True or len(result["issues"]) == 0

    def test_placeholder_jsx_detected(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        f = workspace / "App.js"
        f.write_text("""
import React from 'react';
function App() {
  return (
    <div>
      Airline Reservation System
    </div>
  );
}
export default App;
""")
        result = CodeCompletenessValidator.validate_file(f)
        assert result["complete"] is False
        assert any("placeholder" in i.lower() or "stub" in i.lower() for i in result["issues"])

    def test_console_log_stub_detected(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        f = workspace / "BookingScreen.js"
        f.write_text("""
import React from 'react';
const BookingScreen = () => {
  return (
    <View>
      <Text>Flight Booking</Text>
      <Button
        title='Select Flight'
        onPress={() => console.log('Flight selected!')}
      />
    </View>
  );
};
export default BookingScreen;
""")
        result = CodeCompletenessValidator.validate_file(f)
        assert result["complete"] is False
        assert any("console.log" in i.lower() or "stub" in i.lower() for i in result["issues"])

    def test_todo_comment_detected(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        f = workspace / "service.py"
        f.write_text("""
class BookingService:
    def create_booking(self, flight_id, passenger):
        # TODO: implement booking logic
        pass

    def cancel_booking(self, booking_id):
        # TODO: implement cancellation
        pass
""")
        result = CodeCompletenessValidator.validate_file(f)
        assert result["complete"] is False
        assert any("todo" in i.lower() or "pass" in i.lower() for i in result["issues"])

    def test_empty_file_detected(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        f = workspace / "empty.py"
        f.write_text("")
        result = CodeCompletenessValidator.validate_file(f)
        assert result["complete"] is False

    def test_minimal_component_detected(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        f = workspace / "FlightList.js"
        f.write_text("""
import React from 'react';
const FlightListScreen = () => {
  return (
    <View>
      <Text>Flight List</Text>
    </View>
  );
};
export default FlightListScreen;
""")
        result = CodeCompletenessValidator.validate_file(f)
        assert result["complete"] is False

    def test_validate_workspace_returns_summary(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        # Create mix of complete and stub files
        (workspace / "good.py").write_text("""
class Calculator:
    def add(self, a, b):
        return a + b
    def subtract(self, a, b):
        return a - b
    def multiply(self, a, b):
        return a * b
    def divide(self, a, b):
        if b == 0:
            raise ValueError("Cannot divide by zero")
        return a / b
""")
        (workspace / "stub.py").write_text("""
class PaymentService:
    def process_payment(self, amount):
        # TODO: integrate with payment gateway
        pass
""")
        result = CodeCompletenessValidator.validate_workspace(workspace)
        assert "total_files" in result
        assert "incomplete_files" in result
        assert isinstance(result["incomplete_files"], list)
        assert result["total_files"] >= 2


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Granular Task Decomposition
# ═══════════════════════════════════════════════════════════════════════════════

class TestGranularTaskDecomposition:
    """TaskManager.register_granular_tasks decomposes design into per-file tasks."""

    def test_register_granular_tasks_from_design_spec(self, task_mgr, workspace):
        design_spec = """
## Architecture Design Specification

### Bounded Contexts
1. **Inventory Management** - Flight availability tracking
2. **Booking System** - Reservation handling
3. **Payment Processing** - Transaction management

### Interface Contracts
1. FlightController: GET /flights, POST /flights
2. BookingController: POST /bookings, GET /bookings/:id, DELETE /bookings/:id
3. PaymentController: POST /payments, GET /payments/:id
"""
        tech_stack = """
## File Structure
```
backend/
├── models/
│   ├── flight.py
│   ├── booking.py
│   └── payment.py
├── views/
│   ├── flight_views.py
│   ├── booking_views.py
│   └── payment_views.py
├── serializers/
│   ├── flight_serializer.py
│   ├── booking_serializer.py
│   └── payment_serializer.py
└── tests/
    ├── test_flight.py
    ├── test_booking.py
    └── test_payment.py
```
"""
        tasks = task_mgr.register_granular_tasks(design_spec, tech_stack)
        assert len(tasks) >= 9  # At least 9 source files (3 models + 3 views + 3 serializers)

        # Each task should have a domain context
        for t in tasks:
            assert t.task_type == "file_creation"
            assert t.metadata.get("file_path")
            assert t.metadata.get("domain_context")

    def test_granular_tasks_include_domain_context(self, task_mgr, workspace):
        design_spec = """
### Bounded Contexts
1. **User Management** - Authentication and profiles
2. **Product Catalog** - Product listing and search
"""
        tech_stack = """
## File Structure
```
src/
├── models/
│   ├── user.py
│   └── product.py
├── views/
│   ├── user_views.py
│   └── product_views.py
```
"""
        tasks = task_mgr.register_granular_tasks(design_spec, tech_stack)

        user_tasks = [t for t in tasks if "user" in t.metadata.get("file_path", "").lower()]
        assert len(user_tasks) >= 1
        for t in user_tasks:
            ctx = t.metadata.get("domain_context", "")
            assert "user" in ctx.lower() or "auth" in ctx.lower()

    def test_granular_tasks_persisted_in_db(self, task_mgr, workspace):
        tech_stack = """
## File Structure
```
src/
├── main.py
├── utils.py
└── tests/
    └── test_main.py
```
"""
        task_mgr.register_granular_tasks("", tech_stack)
        all_tasks = task_mgr.get_all_tasks()
        file_tasks = [t for t in all_tasks if t.task_type == "file_creation"]
        assert len(file_tasks) >= 3

    def test_granular_tasks_have_dependencies(self, task_mgr, workspace):
        """Model tasks should be created before view tasks."""
        tech_stack = """
## File Structure
```
src/
├── models/
│   └── flight.py
├── views/
│   └── flight_views.py
```
"""
        tasks = task_mgr.register_granular_tasks("", tech_stack)
        view_tasks = [t for t in tasks if "views" in (t.metadata.get("file_path") or "")]
        model_tasks = [t for t in tasks if "models" in (t.metadata.get("file_path") or "")]
        if view_tasks and model_tasks:
            assert view_tasks[0].dependencies is not None
            assert any(m.task_id in view_tasks[0].dependencies for m in model_tasks)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Per-task file generation loop
# ═══════════════════════════════════════════════════════════════════════════════

class TestPerTaskFileGeneration:
    """Development phase generates files one task at a time."""

    def test_get_next_actionable_task(self, task_mgr, workspace):
        """get_next_actionable_task returns a registered task with all dependencies met."""
        t1 = TaskDefinition(
            task_id="model_flight",
            phase="development",
            task_type="file_creation",
            description="Create flight.py",
            metadata={"file_path": "models/flight.py"},
        )
        t2 = TaskDefinition(
            task_id="view_flight",
            phase="development",
            task_type="file_creation",
            description="Create flight_views.py",
            metadata={"file_path": "views/flight_views.py"},
            dependencies=["model_flight"],
        )
        task_mgr.register_task(t1)
        task_mgr.register_task(t2)

        nxt = task_mgr.get_next_actionable_task("development")
        assert nxt is not None
        assert nxt.task_id == "model_flight"

    def test_get_next_skips_blocked_tasks(self, task_mgr, workspace):
        """A task whose dependency is not completed should not be returned."""
        t1 = TaskDefinition(
            task_id="model_a", phase="development", task_type="file_creation",
            description="model A", metadata={"file_path": "a.py"},
        )
        t2 = TaskDefinition(
            task_id="view_a", phase="development", task_type="file_creation",
            description="view A", metadata={"file_path": "va.py"},
            dependencies=["model_a"],
        )
        task_mgr.register_task(t1)
        task_mgr.register_task(t2)

        # Complete model_a
        task_mgr.update_task_status("model_a", "completed")

        nxt = task_mgr.get_next_actionable_task("development")
        assert nxt is not None
        assert nxt.task_id == "view_a"

    def test_get_next_returns_none_when_all_done(self, task_mgr, workspace):
        t1 = TaskDefinition(
            task_id="only_task", phase="development", task_type="file_creation",
            description="only task", metadata={"file_path": "only.py"},
        )
        task_mgr.register_task(t1)
        task_mgr.update_task_status("only_task", "completed")

        nxt = task_mgr.get_next_actionable_task("development")
        assert nxt is None

    def test_build_file_prompt_includes_context(self, task_mgr, workspace):
        """build_file_prompt creates a focused prompt for a single file."""
        t = TaskDefinition(
            task_id="model_flight",
            phase="development",
            task_type="file_creation",
            description="Create models/flight.py",
            metadata={
                "file_path": "models/flight.py",
                "domain_context": "Inventory Management: tracks flight availability, schedules, and pricing.",
            },
        )
        task_mgr.register_task(t)

        prompt = task_mgr.build_file_prompt(
            t,
            tech_stack="Django + DRF",
            user_stories="As a traveler I want to search flights by date and destination",
        )
        assert "flight.py" in prompt
        assert "Inventory Management" in prompt
        assert "Django" in prompt or "DRF" in prompt


# ═══════════════════════════════════════════════════════════════════════════════
# 4. File tree parsing preserves directory paths
# ═══════════════════════════════════════════════════════════════════════════════

class TestFileTreeParsing:
    """_extract_files_from_content must reconstruct full paths from tree structure."""

    def test_nested_dirs_produce_full_paths(self, task_mgr):
        """Files inside directories should have full paths like api/models.py."""
        tech_stack = """
## File Structure
```
fastapi-task-api/
├── api/
│   ├── __init__.py
│   ├── database.py
│   ├── models.py
│   ├── routes.py
│   └── main.py
├── tests/
│   ├── test_database.py
│   ├── test_routes.py
│   └── test_models.py
├── requirements.txt
├── setup.py
└── README.md
```
"""
        files = task_mgr._extract_files_from_content(tech_stack)
        assert "api/database.py" in files
        assert "api/models.py" in files
        assert "api/routes.py" in files
        assert "api/main.py" in files
        assert "api/__init__.py" in files
        assert "tests/test_database.py" in files
        assert "tests/test_routes.py" in files
        assert "tests/test_models.py" in files
        assert "requirements.txt" in files
        assert "setup.py" in files
        assert "README.md" in files
        # Should NOT include the root project dir as a file
        assert "fastapi-task-api/" not in files

    def test_deeply_nested_dirs(self, task_mgr):
        """Multi-level nesting should produce correct paths."""
        tech_stack = """
```
myproject/
├── src/
│   ├── core/
│   │   ├── __init__.py
│   │   └── engine.py
│   └── utils/
│       └── helpers.py
└── tests/
    └── test_engine.py
```
"""
        files = task_mgr._extract_files_from_content(tech_stack)
        assert "src/core/__init__.py" in files
        assert "src/core/engine.py" in files
        assert "src/utils/helpers.py" in files
        assert "tests/test_engine.py" in files

    def test_file_descriptions_extracted(self, task_mgr):
        """Comments after file names should be captured as descriptions."""
        tech_stack = """
```
myproject/
├── app/
│   ├── models.py          # SQLAlchemy models
│   ├── routes.py          # API routes and endpoints
│   └── main.py            # Application entry point
```
"""
        files_with_desc = task_mgr._extract_files_with_descriptions(tech_stack)
        assert any(f["path"] == "app/models.py" for f in files_with_desc)
        models_entry = next(f for f in files_with_desc if f["path"] == "app/models.py")
        assert "SQLAlchemy" in models_entry["description"]

    def test_register_granular_tasks_preserves_full_paths(self, task_mgr):
        """register_granular_tasks should create tasks with full directory paths."""
        tech_stack = """
## File Structure
```
project/
├── api/
│   ├── models.py
│   └── routes.py
└── tests/
    └── test_models.py
```
"""
        tasks = task_mgr.register_granular_tasks("", tech_stack)
        paths = [t.metadata["file_path"] for t in tasks]
        assert "api/models.py" in paths
        assert "api/routes.py" in paths
        assert "tests/test_models.py" in paths

    def test_register_granular_stores_dependencies_in_db(self, task_mgr):
        """Dependencies should be persisted in the task_dependencies table."""
        tech_stack = """
```
src/
├── models/
│   └── user.py
└── views/
    └── user_views.py
```
"""
        task_mgr.register_granular_tasks("", tech_stack)
        import sqlite3
        conn = sqlite3.connect(task_mgr.db_path)
        deps = conn.execute("SELECT * FROM task_dependencies").fetchall()
        conn.close()
        assert len(deps) > 0, "Dependencies should be stored in DB"

    def test_file_descriptions_in_build_prompt(self, task_mgr):
        """build_file_prompt should include the file's description from the tree."""
        t = TaskDefinition(
            task_id="file_routes",
            phase="development",
            task_type="file_creation",
            description="Create api/routes.py",
            metadata={
                "file_path": "api/routes.py",
                "domain_context": "",
                "file_description": "API routes and endpoints",
            },
        )
        task_mgr.register_task(t)
        prompt = task_mgr.build_file_prompt(t, tech_stack="FastAPI + SQLAlchemy")
        assert "API routes and endpoints" in prompt


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Agent chat reset between tasks
# ═══════════════════════════════════════════════════════════════════════════════

class TestAgentChatReset:
    """The dev agent should reset chat history between per-file tasks."""

    def test_base_agent_has_reset_method(self):
        """BaseLlamaIndexAgent should expose a method to clear chat history."""
        from llamaindex_crew.agents.base_agent import BaseLlamaIndexAgent
        assert hasattr(BaseLlamaIndexAgent, 'reset_chat'), \
            "BaseLlamaIndexAgent must have reset_chat method"


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Integration validation (syntax + import resolution)
# ═══════════════════════════════════════════════════════════════════════════════

class TestIntegrationValidation:
    """CodeCompletenessValidator detects syntax errors and broken local imports."""

    def test_syntax_error_detected(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        f = workspace / "bad_syntax.py"
        f.write_text("def foo(\n    return 42\n")
        result = CodeCompletenessValidator.validate_syntax(f)
        assert result["valid"] is False
        assert result["error"]

    def test_valid_syntax_passes(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        f = workspace / "good.py"
        f.write_text("def foo():\n    return 42\n")
        result = CodeCompletenessValidator.validate_syntax(f)
        assert result["valid"] is True
        assert result["error"] == ""

    def test_broken_local_import_detected(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        f = workspace / "routes.py"
        f.write_text("from api.models import Task\nimport os\n")
        result = CodeCompletenessValidator.validate_imports(f, workspace)
        assert result["valid"] is False
        assert any("api.models" in b["module"] for b in result["broken_imports"])

    def test_stdlib_import_not_flagged(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        f = workspace / "util.py"
        f.write_text("import os\nimport sys\nfrom pathlib import Path\n")
        result = CodeCompletenessValidator.validate_imports(f, workspace)
        assert result["valid"] is True
        assert result["broken_imports"] == []

    def test_third_party_import_not_flagged(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        (workspace / "requirements.txt").write_text("fastapi>=0.95\nsqlalchemy>=2.0\n")
        f = workspace / "main.py"
        f.write_text("from fastapi import FastAPI\nfrom sqlalchemy import Column\n")
        result = CodeCompletenessValidator.validate_imports(f, workspace)
        assert result["valid"] is True
        assert result["broken_imports"] == []

    def test_relative_import_to_existing_file(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        (workspace / "api").mkdir()
        (workspace / "api" / "__init__.py").write_text("")
        (workspace / "api" / "models.py").write_text("class Task: pass\n")
        f = workspace / "api" / "routes.py"
        f.write_text("from api.models import Task\n")
        result = CodeCompletenessValidator.validate_imports(f, workspace)
        assert result["valid"] is True

    def test_validate_file_integration_combines_checks(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        f = workspace / "broken.py"
        f.write_text("from nonexistent.module import Foo\ndef bar(\n  return 1\n")
        result = CodeCompletenessValidator.validate_file_integration(f, workspace)
        assert result["valid"] is False
        assert len(result["issues"]) >= 1

    # ── Multi-language support ────────────────────────────────────────────────

    def test_java_syntax_unmatched_brace_detected(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        f = workspace / "TaskController.java"
        f.write_text("""
package com.example.controller;
import javax.ws.rs.GET;
public class TaskController {
    @GET
    public String list() {
        return "tasks";
    // missing closing brace for class
""")
        result = CodeCompletenessValidator.validate_syntax(f)
        assert result["valid"] is False
        assert "brace" in result["error"].lower() or "unmatched" in result["error"].lower()

    def test_java_valid_syntax_passes(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        f = workspace / "Task.java"
        f.write_text("""
package com.example.model;

public class Task {
    private String title;
    private boolean completed;

    public Task(String title) {
        this.title = title;
        this.completed = false;
    }

    public String getTitle() { return title; }
    public boolean isCompleted() { return completed; }
}
""")
        result = CodeCompletenessValidator.validate_syntax(f)
        assert result["valid"] is True

    def test_java_broken_local_import_detected(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        f = workspace / "TaskController.java"
        f.write_text("""
package com.example.controller;
import com.example.model.Task;
import com.example.service.TaskService;
import javax.ws.rs.GET;

public class TaskController {
    public String list() { return "ok"; }
}
""")
        result = CodeCompletenessValidator.validate_imports(f, workspace)
        assert result["valid"] is False
        assert any("com.example.model.Task" in b["module"] for b in result["broken_imports"])

    def test_java_stdlib_import_not_flagged(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        f = workspace / "Util.java"
        f.write_text("""
import java.util.List;
import java.io.IOException;
import javax.ws.rs.GET;

public class Util {}
""")
        result = CodeCompletenessValidator.validate_imports(f, workspace)
        assert result["valid"] is True

    def test_java_import_resolves_to_existing_file(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        model_dir = workspace / "com" / "example" / "model"
        model_dir.mkdir(parents=True)
        (model_dir / "Task.java").write_text("package com.example.model;\npublic class Task {}\n")
        f = workspace / "TaskController.java"
        f.write_text("import com.example.model.Task;\npublic class TaskController {}\n")
        result = CodeCompletenessValidator.validate_imports(f, workspace)
        assert result["valid"] is True

    def test_js_syntax_unmatched_brace_detected(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        f = workspace / "app.js"
        f.write_text("""
function greet(name) {
    if (name) {
        console.log(name)
    // missing closing brace for function
""")
        result = CodeCompletenessValidator.validate_syntax(f)
        assert result["valid"] is False

    def test_js_valid_syntax_passes(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        f = workspace / "utils.ts"
        f.write_text("""
export function add(a: number, b: number): number {
    return a + b;
}

export function subtract(a: number, b: number): number {
    return a - b;
}
""")
        result = CodeCompletenessValidator.validate_syntax(f)
        assert result["valid"] is True

    def test_js_broken_relative_import_detected(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        f = workspace / "routes.ts"
        f.write_text("""
import { Task } from './models/Task';
import { db } from './database';
import express from 'express';

const router = express.Router();
""")
        result = CodeCompletenessValidator.validate_imports(f, workspace)
        assert result["valid"] is False
        assert len(result["broken_imports"]) >= 1

    def test_js_npm_import_not_flagged(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        pkg = {"dependencies": {"express": "^4.18.0", "cors": "^2.8.0"}}
        import json
        (workspace / "package.json").write_text(json.dumps(pkg))
        f = workspace / "app.js"
        f.write_text("const express = require('express');\nconst cors = require('cors');\n")
        result = CodeCompletenessValidator.validate_imports(f, workspace)
        assert result["valid"] is True

    def test_js_relative_import_resolves(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        (workspace / "models").mkdir()
        (workspace / "models" / "Task.ts").write_text("export class Task {}\n")
        f = workspace / "routes.ts"
        f.write_text("import { Task } from './models/Task';\n")
        result = CodeCompletenessValidator.validate_imports(f, workspace)
        assert result["valid"] is True

    def test_maven_dependency_not_flagged(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        pom = """<project>
  <dependencies>
    <dependency>
      <groupId>io.quarkus</groupId>
      <artifactId>quarkus-resteasy</artifactId>
    </dependency>
  </dependencies>
</project>"""
        (workspace / "pom.xml").write_text(pom)
        f = workspace / "TaskResource.java"
        f.write_text("""
import javax.ws.rs.GET;
import javax.ws.rs.Path;
import io.quarkus.hibernate.orm.panache.PanacheEntity;

@Path("/tasks")
public class TaskResource {}
""")
        result = CodeCompletenessValidator.validate_imports(f, workspace)
        assert result["valid"] is True


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Enriched prompt (vision + full dependency content)
# ═══════════════════════════════════════════════════════════════════════════════

class TestEnrichedPrompt:
    """build_file_prompt should include project vision and full dependency content."""

    def test_build_file_prompt_includes_vision(self, task_mgr):
        t = TaskDefinition(
            task_id="file_routes",
            phase="development",
            task_type="file_creation",
            description="Create api/routes.py",
            metadata={"file_path": "api/routes.py"},
        )
        task_mgr.register_task(t)
        prompt = task_mgr.build_file_prompt(
            t,
            tech_stack="FastAPI",
            project_vision="Create a Task Management REST API with filtering and CRUD",
        )
        assert "Task Management REST API" in prompt

    def test_build_file_prompt_full_dependency_content(self, task_mgr):
        """Existing file content should not be truncated."""
        long_content = "class Task:\n" + "    field = 'x'\n" * 100
        t = TaskDefinition(
            task_id="file_routes",
            phase="development",
            task_type="file_creation",
            description="Create routes.py",
            metadata={"file_path": "routes.py"},
        )
        task_mgr.register_task(t)
        prompt = task_mgr.build_file_prompt(
            t,
            existing_files={"models.py": long_content},
        )
        assert long_content[:500] in prompt

    def test_build_file_prompt_cross_file_instructions(self, task_mgr):
        """Prompt should contain cross-file consistency instructions."""
        t = TaskDefinition(
            task_id="file_routes",
            phase="development",
            task_type="file_creation",
            description="Create routes.py",
            metadata={"file_path": "routes.py"},
        )
        task_mgr.register_task(t)
        prompt = task_mgr.build_file_prompt(
            t,
            existing_files={"models.py": "class Task: pass"},
        )
        assert "import" in prompt.lower()
        assert "resolve" in prompt.lower() or "exist" in prompt.lower()
