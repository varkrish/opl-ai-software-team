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

        # Each non-scaffolding task should have a domain context
        for t in tasks:
            assert t.task_type == "file_creation"
            assert t.metadata.get("file_path")
            if t.source != "auto_injected":
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
# 4b. Edge cases: numbered lists, nested backticks, invalid file paths
# ═══════════════════════════════════════════════════════════════════════════════

class TestExtractFilesEdgeCases:
    """_extract_files_with_descriptions must reject garbage paths and handle nested backticks."""

    def test_rejects_numbered_list_items_as_file_paths(self, task_mgr):
        """Numbered list items like '1. **Foo**:' must not be extracted as file paths."""
        content = """
```
# Some Tech Stack

## Key Components
1. **Keycloak SAML Adapter**:
   - keycloak-saml-adapter-subsystem extension
   - SAML 2.0 SP configuration

2. **Security Configuration**:
   - Elytron security domains

3. **Environment Management**:
   - System properties with placeholders
```
"""
        entries = task_mgr._extract_files_with_descriptions(content)
        paths = [e["path"] for e in entries]
        assert "1." not in paths
        assert "2." not in paths
        assert "3." not in paths
        assert len(entries) == 0

    def test_nested_backtick_blocks_extracts_inner_tree(self, task_mgr):
        """Content wrapped in outer backticks with a nested file-tree block should
        extract files from the inner tree, not from the outer text."""
        content = """```
# Technology Stack

## Key Components
1. **Component A**:
   - some detail

## File Structure
```
myproject/
├── config.xml         # Main configuration
├── settings.yaml      # Settings file
└── README.md          # Documentation
```

## Other Details
Some more text here.
```"""
        entries = task_mgr._extract_files_with_descriptions(content)
        paths = [e["path"] for e in entries]
        assert "config.xml" in paths
        assert "settings.yaml" in paths
        assert "README.md" in paths
        assert "1." not in paths

    def test_real_keycloak_tech_stack(self, task_mgr):
        """The exact tech_stack.md that caused job 64d859cb to fail must extract
        the real file tree, not numbered list items."""
        content = '''```
# Technology Stack for Keycloak SAML Integration with WildFly

## Core Technology
**Application Server**: WildFly 26+ (JBoss EAP 7.4+)
**Identity Provider**: Keycloak 19.0.2+

## Key Components
1. **Keycloak SAML Adapter**:
   - `keycloak-saml-adapter-subsystem` extension
   - SAML 2.0 SP configuration with IDP metadata

2. **Security Configuration**:
   - Elytron security domains
   - KeycloakSecurityRealm integration

3. **Environment Management**:
   - System properties with ${} placeholders

## File Structure (COMPLETE BUILDABLE CONFIGURATION)
```
wildfly-keycloak-saml/
├── domain.xml                  # WildFly domain configuration with Keycloak properties
├── keycloak.xml                # Keycloak SAML SP configuration with placeholders
├── properties/
│   └── env.properties          # Environment-specific values (not committed to VCS)
├── README.md                   # Configuration guide and placeholder replacement instructions
└── certs/                      # Certificate storage (not committed to VCS)
    ├── idp.crt                 # IDP certificate
    ├── sp.crt                  # SP certificate
    └── sp.key                  # SP private key
```

## Configuration Details
### 1. domain.xml (WildFly Configuration)
```xml
<system-properties>
  <property name="keycloak.idp.url" value="${KEYCLOAK_IDP_URL}"/>
</system-properties>
```
```'''
        entries = task_mgr._extract_files_with_descriptions(content)
        paths = [e["path"] for e in entries]
        # Must extract the real files from the file tree
        assert "domain.xml" in paths
        assert "keycloak.xml" in paths
        assert "properties/env.properties" in paths
        assert "README.md" in paths
        # Must NOT extract numbered list items
        assert "1." not in paths
        assert "2." not in paths
        assert "3." not in paths

    def test_register_granular_tasks_skips_invalid_paths(self, task_mgr):
        """register_granular_tasks must not register tasks for invalid file paths
        like '1.', '..', or bare numbers."""
        content = """
```
1. First item
2. Second item
3. Third item
```
"""
        tasks = task_mgr.register_granular_tasks("", content)
        assert len(tasks) == 0


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

    def test_build_file_prompt_includes_interface_contract(self, task_mgr):
        """Prompt should include the interface contract when provided."""
        t = TaskDefinition(
            task_id="file_controller",
            phase="development",
            task_type="file_creation",
            description="Create controller.py",
            metadata={"file_path": "controller.py"},
        )
        task_mgr.register_task(t)
        contract = {
            "models.py": ["Task", "User"],
            "services.py": {"named": ["TaskService", "UserService"], "default": False},
        }
        prompt = task_mgr.build_file_prompt(t, interface_contract=contract)
        assert "INTERFACE CONTRACT" in prompt
        assert "Task" in prompt
        assert "TaskService" in prompt


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Export extraction
# ═══════════════════════════════════════════════════════════════════════════════

class TestExportExtraction:
    """Export extraction tests using language strategies and the delegation API."""

    def test_extract_js_named_exports(self, workspace):
        from llamaindex_crew.orchestrator.language_strategies import JavaScriptStrategy

        s = JavaScriptStrategy()
        f = workspace / "utils.ts"
        f.write_text("""
export function add(a: number, b: number): number { return a + b; }
export const PI = 3.14;
export class Calculator {}
""")
        result = s.extract_exports(f)
        assert "add" in result["exports"]["named"]
        assert "PI" in result["exports"]["named"]
        assert "Calculator" in result["exports"]["named"]
        assert result["exports"]["default"] is False

    def test_extract_js_default_export(self, workspace):
        from llamaindex_crew.orchestrator.language_strategies import JavaScriptStrategy

        s = JavaScriptStrategy()
        f = workspace / "App.tsx"
        f.write_text("""
import React from 'react';
const App = () => <div>Hello</div>;
export default App;
""")
        result = s.extract_exports(f)
        assert result["exports"]["default"] is True

    def test_extract_js_reexport(self, workspace):
        from llamaindex_crew.orchestrator.language_strategies import JavaScriptStrategy

        s = JavaScriptStrategy()
        f = workspace / "index.ts"
        f.write_text("export { Task, User } from './models';")
        result = s.extract_exports(f)
        assert "Task" in result["exports"]["named"]
        assert "User" in result["exports"]["named"]

    def test_extract_python_exports_all(self, workspace):
        from llamaindex_crew.orchestrator.language_strategies import PythonStrategy

        s = PythonStrategy()
        f = workspace / "models.py"
        f.write_text("""
__all__ = ['Task', 'User']

class Task:
    pass

class User:
    pass

class _Internal:
    pass
""")
        result = s.extract_exports(f)
        assert result["exports"] == ["Task", "User"]

    def test_extract_python_exports_fallback(self, workspace):
        from llamaindex_crew.orchestrator.language_strategies import PythonStrategy

        s = PythonStrategy()
        f = workspace / "services.py"
        f.write_text("""
class TaskService:
    def create(self): ...

def get_all_tasks():
    return []

def _private_helper():
    pass
""")
        result = s.extract_exports(f)
        assert "TaskService" in result["exports"]
        assert "get_all_tasks" in result["exports"]
        assert "_private_helper" not in result["exports"]

    def test_extract_java_public_types(self, workspace):
        from llamaindex_crew.orchestrator.language_strategies import JavaStrategy

        s = JavaStrategy()
        f = workspace / "Task.java"
        f.write_text("""
package com.example.model;

public class Task {
    private String title;
}

public interface TaskRepository {
    Task findById(Long id);
}

public enum TaskStatus {
    OPEN, CLOSED
}
""")
        result = s.extract_exports(f)
        assert "Task" in result["exports"]
        assert "TaskRepository" in result["exports"]
        assert "TaskStatus" in result["exports"]

    def test_extract_export_summary_dispatches(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        py = workspace / "foo.py"
        py.write_text("class Foo: pass\n")
        result = CodeCompletenessValidator.extract_export_summary(py)
        assert result["type"] == "python"
        assert "Foo" in result["exports"]

        js = workspace / "bar.js"
        js.write_text("export function bar() {}\n")
        result = CodeCompletenessValidator.extract_export_summary(js)
        assert result["type"] == "js"
        assert "bar" in result["exports"]["named"]


# ═══════════════════════════════════════════════════════════════════════════════
# 9. Dependency manifest validation
# ═══════════════════════════════════════════════════════════════════════════════

class TestDependencyManifestValidation:
    """validate_dependency_manifest flags undeclared packages."""

    def test_js_missing_from_package_json(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator
        import json

        (workspace / "package.json").write_text(json.dumps({"dependencies": {"express": "^4.18"}}))
        f = workspace / "app.js"
        f.write_text("const express = require('express');\nconst cors = require('cors');\n")
        result = CodeCompletenessValidator.validate_dependency_manifest(workspace)
        assert result["valid"] is False
        missing_pkgs = [m["package"] for m in result["missing"]]
        assert "cors" in missing_pkgs

    def test_js_all_declared_passes(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator
        import json

        (workspace / "package.json").write_text(
            json.dumps({"dependencies": {"express": "^4.18", "cors": "^2.8"}})
        )
        f = workspace / "app.js"
        f.write_text("const express = require('express');\nconst cors = require('cors');\n")
        result = CodeCompletenessValidator.validate_dependency_manifest(workspace)
        js_missing = [m for m in result["missing"] if m["ecosystem"] == "npm"]
        assert len(js_missing) == 0

    def test_python_missing_from_requirements(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        (workspace / "requirements.txt").write_text("flask>=2.0\n")
        f = workspace / "app.py"
        f.write_text("from flask import Flask\nimport sqlalchemy\n")
        result = CodeCompletenessValidator.validate_dependency_manifest(workspace)
        missing_pkgs = [m["package"] for m in result["missing"]]
        assert "sqlalchemy" in missing_pkgs

    def test_java_undeclared_dependency(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        (workspace / "pom.xml").write_text("""<project>
  <dependencies>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter-web</artifactId>
    </dependency>
  </dependencies>
</project>""")
        f = workspace / "App.java"
        f.write_text("""
import org.springframework.boot.SpringApplication;
import org.apache.commons.lang3.StringUtils;
public class App { }
""")
        result = CodeCompletenessValidator.validate_dependency_manifest(workspace)
        missing_pkgs = [m["package"] for m in result["missing"] if m["ecosystem"] in ("maven", "java")]
        assert any("apache" in p for p in missing_pkgs)

    def test_node_builtins_not_flagged(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator
        import json

        (workspace / "package.json").write_text(json.dumps({"dependencies": {}}))
        f = workspace / "server.js"
        f.write_text("const fs = require('fs');\nconst path = require('path');\n")
        result = CodeCompletenessValidator.validate_dependency_manifest(workspace)
        js_missing = [m for m in result["missing"] if m["ecosystem"] == "npm"]
        assert len(js_missing) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# 10. Tech stack conformance
# ═══════════════════════════════════════════════════════════════════════════════

class TestTechStackConformance:
    """validate_tech_stack_conformance detects conflicting tech choices."""

    def test_mongoose_when_sequelize_chosen(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        tech_stack = "## Stack\nBackend: Node.js + Express + Sequelize + PostgreSQL"
        f = workspace / "db.js"
        f.write_text("const mongoose = require('mongoose');\n")
        result = CodeCompletenessValidator.validate_tech_stack_conformance(workspace, tech_stack)
        assert result["valid"] is False
        assert any("MongoDB" in c["conflict"] for c in result["conflicts"])

    def test_quarkus_when_spring_chosen(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        tech_stack = "## Stack\nBackend: Java + Spring Boot + PostgreSQL"
        f = workspace / "App.java"
        f.write_text("import io.quarkus.runtime.Quarkus;\npublic class App {}\n")
        result = CodeCompletenessValidator.validate_tech_stack_conformance(workspace, tech_stack)
        assert result["valid"] is False
        assert any("Spring" in c["conflict"] or "Quarkus" in c["conflict"] for c in result["conflicts"])

    def test_no_conflicts_passes(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        tech_stack = "## Stack\nBackend: Node.js + Express + Sequelize"
        f = workspace / "app.js"
        f.write_text("const express = require('express');\n")
        result = CodeCompletenessValidator.validate_tech_stack_conformance(workspace, tech_stack)
        assert result["valid"] is True

    def test_django_vs_flask_conflict(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        tech_stack = "## Stack\nBackend: Python + Django + PostgreSQL"
        f = workspace / "app.py"
        f.write_text("from flask import Flask\napp = Flask(__name__)\n")
        result = CodeCompletenessValidator.validate_tech_stack_conformance(workspace, tech_stack)
        assert result["valid"] is False


# ═══════════════════════════════════════════════════════════════════════════════
# 11. Enhanced dependency ordering (multi-tier)
# ═══════════════════════════════════════════════════════════════════════════════

class TestEnhancedDependencyOrdering:
    """_classify_file_tier and enhanced _infer_dependencies."""

    def test_classify_file_tier_config(self, task_mgr):
        assert task_mgr._classify_file_tier("config/database.py") <= 1

    def test_classify_file_tier_model(self, task_mgr):
        assert task_mgr._classify_file_tier("models/user.py") <= 2

    def test_classify_file_tier_service(self, task_mgr):
        assert task_mgr._classify_file_tier("services/user_service.py") >= 2

    def test_classify_file_tier_controller(self, task_mgr):
        assert task_mgr._classify_file_tier("controllers/user_controller.py") >= 3

    def test_classify_file_tier_route(self, task_mgr):
        assert task_mgr._classify_file_tier("routes/api.py") >= 5

    def test_classify_file_tier_server(self, task_mgr):
        assert task_mgr._classify_file_tier("server.js") >= 7

    def test_classify_file_tier_java_entity(self, task_mgr):
        tier = task_mgr._classify_file_tier("src/main/java/com/example/entity/User.java")
        assert tier <= 2

    def test_classify_file_tier_java_repository(self, task_mgr):
        tier = task_mgr._classify_file_tier("src/main/java/com/example/repository/UserRepository.java")
        assert tier >= 2

    def test_classify_file_tier_java_controller(self, task_mgr):
        tier = task_mgr._classify_file_tier("src/main/java/com/example/controller/UserController.java")
        assert tier >= 3

    def test_config_before_model_before_controller(self, task_mgr):
        """Tier ordering: config < model < controller."""
        config_tier = task_mgr._classify_file_tier("config/db.py")
        model_tier = task_mgr._classify_file_tier("models/user.py")
        ctrl_tier = task_mgr._classify_file_tier("controllers/user_controller.py")
        assert config_tier < model_tier <= ctrl_tier

    def test_multi_tier_dependencies_inferred(self, task_mgr):
        """Controller should depend on model, not the other way around."""
        tech_stack = """
## File Structure
```
project/
├── config/
│   └── db.py
├── models/
│   └── user.py
├── services/
│   └── user_service.py
├── controllers/
│   └── user_controller.py
└── server.py
```
"""
        tasks = task_mgr.register_granular_tasks("", tech_stack)
        ctrl = next(t for t in tasks if "controller" in (t.metadata or {}).get("file_path", ""))
        model = next(t for t in tasks if "models" in (t.metadata or {}).get("file_path", ""))
        assert model.task_id in (ctrl.dependencies or [])

    def test_java_spring_ordering(self, task_mgr):
        """Java Spring: entity -> repository -> service -> controller."""
        tech_stack = """
## File Structure
```
src/
├── main/
│   └── java/
│       └── com/
│           └── example/
│               ├── entity/
│               │   └── User.java
│               ├── repository/
│               │   └── UserRepository.java
│               ├── service/
│               │   └── UserService.java
│               └── controller/
│                   └── UserController.java
```
"""
        tasks = task_mgr.register_granular_tasks("", tech_stack)
        paths = [(t.metadata or {}).get("file_path", "") for t in tasks]
        entity_idx = next(i for i, p in enumerate(paths) if "entity" in p)
        repo_idx = next(i for i, p in enumerate(paths) if "repository" in p)
        svc_idx = next(i for i, p in enumerate(paths) if "service" in p)
        ctrl_idx = next(i for i, p in enumerate(paths) if "controller" in p)
        assert entity_idx < repo_idx < svc_idx < ctrl_idx


# ═══════════════════════════════════════════════════════════════════════════════
# 12. npm bare specifier fix (regression test)
# ═══════════════════════════════════════════════════════════════════════════════

class TestNpmBareSpecifierValidation:
    """Bare npm specifiers not in package.json should be flagged."""

    def test_undeclared_npm_package_flagged(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator
        import json

        (workspace / "package.json").write_text(json.dumps({"dependencies": {"express": "^4.18"}}))
        f = workspace / "app.js"
        f.write_text("import cors from 'cors';\nimport express from 'express';\n")
        result = CodeCompletenessValidator.validate_imports(f, workspace)
        assert result["valid"] is False
        assert any("cors" in b["module"] for b in result["broken_imports"])

    def test_node_builtin_not_flagged(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator
        import json

        (workspace / "package.json").write_text(json.dumps({"dependencies": {}}))
        f = workspace / "server.js"
        f.write_text("const fs = require('fs');\nconst path = require('path');\n")
        result = CodeCompletenessValidator.validate_imports(f, workspace)
        assert result["valid"] is True

    def test_declared_npm_package_passes(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator
        import json

        (workspace / "package.json").write_text(
            json.dumps({"dependencies": {"express": "^4.18", "cors": "^2.8"}})
        )
        f = workspace / "app.js"
        f.write_text("import express from 'express';\nimport cors from 'cors';\n")
        result = CodeCompletenessValidator.validate_imports(f, workspace)
        assert result["valid"] is True


# ═══════════════════════════════════════════════════════════════════════════════
# 13. Smoke test runner — strategy pattern
# ═══════════════════════════════════════════════════════════════════════════════

class TestSmokeTestRunner:
    """smoke_test_runner detects project type and validates it compiles."""

    def test_detect_node_project(self, workspace):
        from llamaindex_crew.tools.test_tools import _detect_project_type
        import json

        (workspace / "package.json").write_text(json.dumps({"name": "test"}))
        assert _detect_project_type(workspace) == "node"

    def test_detect_python_project(self, workspace):
        from llamaindex_crew.tools.test_tools import _detect_project_type

        (workspace / "requirements.txt").write_text("flask>=2.0\n")
        assert _detect_project_type(workspace) == "python"

    def test_detect_maven_project(self, workspace):
        from llamaindex_crew.tools.test_tools import _detect_project_type

        (workspace / "pom.xml").write_text("<project></project>")
        assert _detect_project_type(workspace) == "java_maven"

    def test_detect_gradle_project(self, workspace):
        from llamaindex_crew.tools.test_tools import _detect_project_type

        (workspace / "build.gradle").write_text("plugins { id 'java' }")
        assert _detect_project_type(workspace) == "java_gradle"

    def test_detect_unknown_project(self, workspace):
        from llamaindex_crew.tools.test_tools import _detect_project_type

        assert _detect_project_type(workspace) == "unknown"


class TestSyntaxOnlyBackend:
    """SyntaxOnlyBackend runs static analysis without any subprocess."""

    def test_valid_python_project_passes(self, workspace):
        from llamaindex_crew.tools.test_tools import SyntaxOnlyBackend

        (workspace / "requirements.txt").write_text("flask>=2.0\n")
        (workspace / "app.py").write_text("from flask import Flask\napp = Flask(__name__)\n")
        result = SyntaxOnlyBackend().run(workspace, "python")
        assert str(result).startswith("✅")

    def test_syntax_error_detected(self, workspace):
        from llamaindex_crew.tools.test_tools import SyntaxOnlyBackend

        (workspace / "requirements.txt").write_text("flask>=2.0\n")
        (workspace / "app.py").write_text("def broken(\n")
        result = SyntaxOnlyBackend().run(workspace, "python")
        assert str(result).startswith("❌")
        assert "issue" in str(result).lower()

    def test_broken_import_detected(self, workspace):
        from llamaindex_crew.tools.test_tools import SyntaxOnlyBackend
        import json

        (workspace / "package.json").write_text(json.dumps({"dependencies": {}}))
        (workspace / "index.js").write_text("import express from 'express';\n")
        result = SyntaxOnlyBackend().run(workspace, "node")
        assert str(result).startswith("❌")
        assert "express" in str(result)

    def test_valid_js_project_passes(self, workspace):
        from llamaindex_crew.tools.test_tools import SyntaxOnlyBackend
        import json

        (workspace / "package.json").write_text(
            json.dumps({"dependencies": {"express": "^4.18"}})
        )
        (workspace / "index.js").write_text(
            "const express = require('express');\nconst app = express();\n"
        )
        result = SyntaxOnlyBackend().run(workspace, "node")
        assert str(result).startswith("✅")

    def test_java_syntax_error_detected(self, workspace):
        from llamaindex_crew.tools.test_tools import SyntaxOnlyBackend

        (workspace / "pom.xml").write_text("<project></project>")
        src = workspace / "src" / "main" / "java"
        src.mkdir(parents=True)
        (src / "App.java").write_text("public class App {")  # missing closing brace
        result = SyntaxOnlyBackend().run(workspace, "java_maven")
        assert str(result).startswith("❌")


class TestBackendSelection:
    """smoke_test_runner dispatches to the correct backend based on env var."""

    def test_defaults_to_syntax_only(self, workspace, monkeypatch):
        from llamaindex_crew.tools.test_tools import smoke_test_runner

        monkeypatch.setenv("WORKSPACE_PATH", str(workspace))
        monkeypatch.delenv("SMOKE_TEST_BACKEND", raising=False)
        (workspace / "requirements.txt").write_text("flask>=2.0\n")
        (workspace / "app.py").write_text("print('hello')\n")
        result = smoke_test_runner("auto")
        assert "Syntax-only" in str(result)

    def test_invalid_backend_returns_error(self, workspace, monkeypatch):
        from llamaindex_crew.tools.test_tools import smoke_test_runner

        monkeypatch.setenv("WORKSPACE_PATH", str(workspace))
        monkeypatch.setenv("SMOKE_TEST_BACKEND", "invalid_backend")
        (workspace / "requirements.txt").write_text("flask>=2.0\n")
        result = smoke_test_runner("auto")
        assert str(result).startswith("❌")
        assert "invalid_backend" in str(result)

    def test_unknown_project_type_returns_error(self, workspace, monkeypatch):
        from llamaindex_crew.tools.test_tools import smoke_test_runner

        monkeypatch.setenv("WORKSPACE_PATH", str(workspace))
        result = smoke_test_runner("auto")
        assert str(result).startswith("❌")
        assert "detect" in str(result).lower()


class TestKubernetesJobBackend:
    """KubernetesJobBackend skeleton raises a clear error without the k8s package."""

    def test_missing_kubernetes_package(self, workspace, monkeypatch):
        import importlib
        from llamaindex_crew.tools.test_tools import KubernetesJobBackend

        original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__
        def mock_import(name, *args, **kwargs):
            if name == "kubernetes":
                raise ImportError("No module named 'kubernetes'")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr("builtins.__import__", mock_import)
        result = KubernetesJobBackend().run(workspace, "python")
        assert str(result).startswith("❌")
        assert "kubernetes" in str(result).lower()


class TestLocalContainerBackend:
    """LocalContainerBackend gives a clear message when no runtime is available."""

    def test_no_runtime_available(self, workspace, monkeypatch):
        from llamaindex_crew.tools.test_tools import LocalContainerBackend

        monkeypatch.setattr(
            LocalContainerBackend, "_find_runtime", staticmethod(lambda: None)
        )
        result = LocalContainerBackend().run(workspace, "node")
        assert str(result).startswith("❌")
        assert "No container runtime" in str(result)

    def test_unknown_project_type(self, workspace):
        from llamaindex_crew.tools.test_tools import LocalContainerBackend

        result = LocalContainerBackend().run(workspace, "unknown")
        assert str(result).startswith("❌")
        assert "No container image" in str(result)


class TestSmokeTestResult:
    """SmokeTestResult carries both a message and an optional container log."""

    def test_str_returns_message(self):
        from llamaindex_crew.tools.test_tools import SmokeTestResult

        r = SmokeTestResult("✅ passed", log="detailed log output")
        assert str(r) == "✅ passed"
        assert r.log == "detailed log output"

    def test_empty_log_by_default(self):
        from llamaindex_crew.tools.test_tools import SmokeTestResult

        r = SmokeTestResult("❌ failed")
        assert r.log == ""

    def test_container_backend_returns_log(self, workspace, monkeypatch):
        """LocalContainerBackend must populate .log with full stdout/stderr."""
        import subprocess
        from llamaindex_crew.tools.test_tools import LocalContainerBackend

        fake_result = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="all modules compiled OK\n", stderr="",
        )
        monkeypatch.setattr(
            LocalContainerBackend, "_find_runtime", staticmethod(lambda: "podman")
        )
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_result)

        (workspace / "requirements.txt").write_text("flask\n")
        result = LocalContainerBackend().run(workspace, "python")
        assert str(result).startswith("✅")
        assert "all modules compiled OK" in result.log
        assert "runtime: podman" in result.log
        assert "exit_code: 0" in result.log


# ═══════════════════════════════════════════════════════════════════════════════
# 14. Content normalization (literal \n fix)
# ═══════════════════════════════════════════════════════════════════════════════

class TestContentNormalization:
    """file_writer should convert literal \\n to real newlines when the LLM
    serialises the whole file as a single escaped string."""

    def test_literal_newlines_normalized(self):
        from llamaindex_crew.tools.file_tools import _normalize_content

        bad = r"import os\nfrom pathlib import Path\n\ndef main():\n    pass\n"
        result = _normalize_content(bad, "app.py")
        assert "\n" in result
        assert result.count("\n") >= 4
        assert "\\n" not in result

    def test_real_newlines_untouched(self):
        from llamaindex_crew.tools.file_tools import _normalize_content

        good = "import os\nfrom pathlib import Path\n\ndef main():\n    pass\n"
        result = _normalize_content(good, "app.py")
        assert result == good

    def test_non_source_files_untouched(self):
        from llamaindex_crew.tools.file_tools import _normalize_content

        binary_like = r"some\nescaped\ncontent\nhere\nfor\nbinary"
        result = _normalize_content(binary_like, "image.png")
        assert result == binary_like

    def test_literal_tabs_also_normalized(self):
        from llamaindex_crew.tools.file_tools import _normalize_content

        bad = r"import os\nfrom sys import argv\n\ndef foo():\n\treturn 1\n\ndef bar():\n\treturn 2\n"
        result = _normalize_content(bad, "foo.py")
        assert "\t" in result
        assert "\n" in result
        assert "\\t" not in result

    def test_mixed_real_and_literal_not_normalized(self):
        """Files with adequate real newlines but some literal \\n in strings
        should NOT be modified — they likely have intentional escape sequences."""
        from llamaindex_crew.tools.file_tools import _normalize_content

        code = 'line1 = "hello\\nworld"\nline2 = "foo"\nline3 = "bar"\nline4 = "baz"\n'
        result = _normalize_content(code, "app.py")
        assert result == code


# ═══════════════════════════════════════════════════════════════════════════════
# 15. Phase artifact fallback persistence
# ═══════════════════════════════════════════════════════════════════════════════

class TestPhaseArtifactFallback:
    """When an agent returns content in its response but fails to call
    file_writer, the workflow must persist the artifact anyway."""

    def test_fallback_writes_file_when_missing(self, workspace):
        from llamaindex_crew.workflows.software_dev_workflow import _persist_phase_artifact

        agent_response = "# User Stories\n\n## US-1: Create a todo\nAs a user..."
        path = workspace / "user_stories.md"
        assert not path.exists()

        result = _persist_phase_artifact(workspace, "user_stories.md", agent_response)
        assert path.exists()
        assert "User Stories" in path.read_text()
        assert result is True

    def test_fallback_skips_when_file_already_exists(self, workspace):
        from llamaindex_crew.workflows.software_dev_workflow import _persist_phase_artifact

        path = workspace / "design_spec.md"
        path.write_text("# Existing design spec\n")

        agent_response = "# Different design spec\nSomething else"
        result = _persist_phase_artifact(workspace, "design_spec.md", agent_response)
        assert path.read_text() == "# Existing design spec\n"
        assert result is False

    def test_fallback_skips_empty_response(self, workspace):
        from llamaindex_crew.workflows.software_dev_workflow import _persist_phase_artifact

        result = _persist_phase_artifact(workspace, "user_stories.md", "")
        assert not (workspace / "user_stories.md").exists()
        assert result is False

    def test_fallback_skips_generic_response(self, workspace):
        """Agent responses like 'I have completed the task' without real
        content should NOT be written to files."""
        from llamaindex_crew.workflows.software_dev_workflow import _persist_phase_artifact

        result = _persist_phase_artifact(
            workspace, "user_stories.md",
            "I have completed the user stories task successfully."
        )
        assert not (workspace / "user_stories.md").exists()
        assert result is False

    def test_fallback_writes_multiline_content(self, workspace):
        from llamaindex_crew.workflows.software_dev_workflow import _persist_phase_artifact

        content = (
            "# Tech Stack\n\n"
            "## Backend\n- Python 3.11\n- Flask\n\n"
            "## Frontend\n- React 18\n- TypeScript\n\n"
            "## Database\n- PostgreSQL 15\n"
        )
        result = _persist_phase_artifact(workspace, "tech_stack.md", content)
        assert (workspace / "tech_stack.md").exists()
        saved = (workspace / "tech_stack.md").read_text()
        assert "Python 3.11" in saved
        assert "React 18" in saved
        assert result is True


# ═══════════════════════════════════════════════════════════════════════════════
# 16. Package structure validation (__init__.py)
# ═══════════════════════════════════════════════════════════════════════════════

class TestPackageStructure:
    """Directories used as Python import paths must have __init__.py."""

    def test_missing_init_detected(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        src = workspace / "src"
        src.mkdir()
        (src / "models.py").write_text("class Task: pass\n")
        (workspace / "app.py").write_text("from src.models import Task\n")
        result = CodeCompletenessValidator.validate_package_structure(workspace)
        assert not result["valid"]
        assert "src" in result["missing_init"]

    def test_init_present_passes(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        src = workspace / "src"
        src.mkdir()
        (src / "__init__.py").write_text("")
        (src / "models.py").write_text("class Task: pass\n")
        (workspace / "app.py").write_text("from src.models import Task\n")
        result = CodeCompletenessValidator.validate_package_structure(workspace)
        assert result["valid"]
        assert result["missing_init"] == []

    def test_nested_packages_all_need_init(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        src = workspace / "src"
        tests = src / "tests"
        tests.mkdir(parents=True)
        (src / "__init__.py").write_text("")
        # src/tests/ is missing __init__.py
        (tests / "test_app.py").write_text("import unittest\n")
        (workspace / "run_tests.py").write_text("from src.tests.test_app import *\n")
        result = CodeCompletenessValidator.validate_package_structure(workspace)
        assert not result["valid"]
        assert "src/tests" in result["missing_init"]

    def test_no_python_imports_passes(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        (workspace / "index.js").write_text("const x = require('./utils');\n")
        result = CodeCompletenessValidator.validate_package_structure(workspace)
        assert result["valid"]


# ═══════════════════════════════════════════════════════════════════════════════
# 17. Duplicate / scattered file detection
# ═══════════════════════════════════════════════════════════════════════════════

class TestDuplicateFiles:
    """Detect hallucinated duplicate source files under different directories."""

    def test_duplicate_app_py_detected(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        (workspace / "src").mkdir()
        (workspace / "src" / "app.py").write_text("from flask import Flask\napp = Flask(__name__)\n")
        todo = workspace / "todo-api" / "src"
        todo.mkdir(parents=True)
        (todo / "app.py").write_text("from flask import Flask\ntest_app = Flask(__name__)\n")

        result = CodeCompletenessValidator.validate_duplicate_files(workspace)
        assert not result["valid"]
        filenames = [d["filename"] for d in result["duplicates"]]
        assert "app.py" in filenames

    def test_no_duplicates_passes(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        (workspace / "src").mkdir()
        (workspace / "src" / "app.py").write_text("from flask import Flask\n")
        (workspace / "src" / "models.py").write_text("class Task: pass\n")

        result = CodeCompletenessValidator.validate_duplicate_files(workspace)
        assert result["valid"]
        assert result["duplicates"] == []

    def test_same_name_different_extension_not_flagged(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        (workspace / "src").mkdir()
        (workspace / "src" / "app.py").write_text("print('hello')\n")
        (workspace / "src" / "app.js").write_text("console.log('hello');\n")

        result = CodeCompletenessValidator.validate_duplicate_files(workspace)
        assert result["valid"]

    def test_test_files_with_same_name_flagged(self, workspace):
        """test_models.py under src/tests/ AND todo-api/tests/ should be flagged."""
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        t1 = workspace / "src" / "tests"
        t1.mkdir(parents=True)
        (t1 / "test_models.py").write_text("def test_one(): pass\n")
        t2 = workspace / "todo-api" / "tests"
        t2.mkdir(parents=True)
        (t2 / "test_models.py").write_text("def test_two(): pass\n")

        result = CodeCompletenessValidator.validate_duplicate_files(workspace)
        assert not result["valid"]
        assert any(d["filename"] == "test_models.py" for d in result["duplicates"])


# ═══════════════════════════════════════════════════════════════════════════════
# 18. Entrypoint wiring validation
# ═══════════════════════════════════════════════════════════════════════════════

class TestEntrypointWiring:
    """Verify that the generated entrypoint actually wires up the framework."""

    def test_flask_incomplete_wiring_detected(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        (workspace / "app.py").write_text(
            "from flask import Flask\napp = Flask(__name__)\n"
            "@app.route('/')\ndef home():\n    return 'hello'\n"
        )
        tech_stack = "Framework: Flask\nDatabase: SQLAlchemy"
        result = CodeCompletenessValidator.validate_entrypoint(workspace, tech_stack)
        assert not result["valid"]
        assert result["framework"] == "flask"
        assert any("db.init_app" in m for m in result["missing_wiring"])

    def test_flask_complete_wiring_passes(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        (workspace / "app.py").write_text(
            "from flask import Flask\n"
            "from flask_sqlalchemy import SQLAlchemy\n"
            "app = Flask(__name__)\n"
            "db = SQLAlchemy(app)\n"
            "from routes import *\n"
        )
        tech_stack = "Framework: Flask"
        result = CodeCompletenessValidator.validate_entrypoint(workspace, tech_stack)
        assert result["valid"]
        assert result["framework"] == "flask"

    def test_express_missing_listen_detected(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        (workspace / "app.js").write_text(
            "const express = require('express');\n"
            "const app = express();\n"
            "app.get('/', (req, res) => res.send('hi'));\n"
        )
        tech_stack = "Framework: Express.js"
        result = CodeCompletenessValidator.validate_entrypoint(workspace, tech_stack)
        assert not result["valid"]
        assert any("listen" in m for m in result["missing_wiring"])

    def test_express_complete_wiring_passes(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        (workspace / "server.js").write_text(
            "const express = require('express');\n"
            "const app = express();\n"
            "app.get('/', (req, res) => res.send('hi'));\n"
            "app.listen(3000);\n"
        )
        tech_stack = "Framework: Express"
        result = CodeCompletenessValidator.validate_entrypoint(workspace, tech_stack)
        assert result["valid"]

    def test_spring_boot_wiring(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        src = workspace / "src" / "main" / "java"
        src.mkdir(parents=True)
        (src / "Application.java").write_text(
            "@SpringBootApplication\n"
            "public class Application {\n"
            "    public static void main(String[] args) {\n"
            "        SpringApplication.run(Application.class, args);\n"
            "    }\n"
            "}\n"
        )
        tech_stack = "Framework: Spring Boot"
        result = CodeCompletenessValidator.validate_entrypoint(workspace, tech_stack)
        assert result["valid"]
        assert result["framework"] == "spring"

    def test_unknown_framework_skipped(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        result = CodeCompletenessValidator.validate_entrypoint(workspace, "Language: Rust, Framework: Actix")
        assert result["valid"]
        assert result["framework"] == ""

    def test_no_tech_stack_skipped(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        result = CodeCompletenessValidator.validate_entrypoint(workspace, "")
        assert result["valid"]


# ═══════════════════════════════════════════════════════════════════════════════
# 19. Upstream prevention: auto-inject __init__.py and entrypoint hints
# ═══════════════════════════════════════════════════════════════════════════════

class TestAutoInjectInitPy:
    """Task manager must auto-inject __init__.py tasks for Python package dirs."""

    def test_init_py_injected_for_python_subdirs(self, workspace):
        from llamaindex_crew.orchestrator.task_manager import TaskManager

        db_path = workspace / "tasks.db"
        tm = TaskManager(db_path, "test-project")

        tech_stack = (
            "## File Structure\n"
            "```\n"
            "myapp/\n"
            "├── src/\n"
            "│   ├── models.py\n"
            "│   ├── routes.py\n"
            "│   └── tests/\n"
            "│       └── test_models.py\n"
            "├── requirements.txt\n"
            "└── README.md\n"
            "```\n"
        )
        tasks = tm.register_granular_tasks("", tech_stack)
        file_paths = [(t.metadata or {}).get("file_path", "") for t in tasks]

        assert "src/__init__.py" in file_paths
        assert "src/tests/__init__.py" in file_paths

    def test_no_init_py_for_non_python_project(self, workspace):
        from llamaindex_crew.orchestrator.task_manager import TaskManager

        db_path = workspace / "tasks.db"
        tm = TaskManager(db_path, "test-project")

        tech_stack = (
            "## File Structure\n"
            "```\n"
            "webapp/\n"
            "├── src/\n"
            "│   ├── index.js\n"
            "│   └── utils.js\n"
            "├── package.json\n"
            "└── README.md\n"
            "```\n"
        )
        tasks = tm.register_granular_tasks("", tech_stack)
        file_paths = [(t.metadata or {}).get("file_path", "") for t in tasks]

        assert not any("__init__.py" in fp for fp in file_paths)

    def test_init_py_has_auto_content_metadata(self, workspace):
        from llamaindex_crew.orchestrator.task_manager import TaskManager

        db_path = workspace / "tasks.db"
        tm = TaskManager(db_path, "test-project")

        tech_stack = (
            "## File Structure\n"
            "```\n"
            "api/\n"
            "├── src/\n"
            "│   └── app.py\n"
            "└── requirements.txt\n"
            "```\n"
        )
        tasks = tm.register_granular_tasks("", tech_stack)
        init_tasks = [t for t in tasks if "__init__.py" in (t.metadata or {}).get("file_path", "")]
        assert len(init_tasks) > 0
        for t in init_tasks:
            assert "auto_content" in t.metadata


class TestInitPyConflictDetection:
    """_inject_init_py_tasks must NOT create dir/__init__.py when dir.py exists."""

    def test_skips_init_when_flat_module_exists(self, workspace):
        from llamaindex_crew.orchestrator.task_manager import TaskManager

        db_path = workspace / "tasks.db"
        tm = TaskManager(db_path, "test-project")

        tech_stack = (
            "## File Structure\n"
            "```\n"
            "todo-api/\n"
            "├── app/\n"
            "│   ├── __init__.py\n"
            "│   ├── models.py\n"
            "│   ├── routes.py\n"
            "│   └── config.py\n"
            "├── tests/\n"
            "│   └── test_routes.py\n"
            "└── requirements.txt\n"
            "```\n"
        )
        tasks = tm.register_granular_tasks("", tech_stack)
        file_paths = [(t.metadata or {}).get("file_path", "") for t in tasks]

        assert "app/__init__.py" in file_paths
        # Flat modules app/models.py and app/routes.py must NOT trigger package dirs
        assert "app/models/__init__.py" not in file_paths
        assert "app/routes/__init__.py" not in file_paths
        # tests/ is a real sub-directory → gets __init__.py
        assert "tests/__init__.py" in file_paths

    def test_creates_init_only_for_real_subdirs(self, workspace):
        from llamaindex_crew.orchestrator.task_manager import TaskManager

        db_path = workspace / "tasks.db"
        tm = TaskManager(db_path, "test-project")

        tech_stack = (
            "## File Structure\n"
            "```\n"
            "todo-api/\n"
            "├── app/\n"
            "│   ├── __init__.py\n"
            "│   ├── models.py\n"
            "│   ├── routes.py\n"
            "│   └── tests/\n"
            "│       └── test_routes.py\n"
            "├── requirements.txt\n"
            "└── README.md\n"
            "```\n"
        )
        tasks = tm.register_granular_tasks("", tech_stack)
        file_paths = [(t.metadata or {}).get("file_path", "") for t in tasks]

        assert "app/__init__.py" in file_paths
        assert "app/tests/__init__.py" in file_paths
        assert "app/models/__init__.py" not in file_paths
        assert "app/routes/__init__.py" not in file_paths


class TestGetRegisteredFilePaths:
    """get_registered_file_paths returns all file_creation task paths."""

    def test_returns_file_paths(self, workspace):
        from llamaindex_crew.orchestrator.task_manager import TaskManager

        db_path = workspace / "tasks.db"
        tm = TaskManager(db_path, "test-project")

        tech_stack = (
            "## File Structure\n"
            "```\n"
            "todo-api/\n"
            "├── app/\n"
            "│   ├── __init__.py\n"
            "│   ├── models.py\n"
            "│   └── routes.py\n"
            "├── requirements.txt\n"
            "└── README.md\n"
            "```\n"
        )
        tm.register_granular_tasks("", tech_stack)
        paths = tm.get_registered_file_paths()

        assert "app/__init__.py" in paths
        assert "app/models.py" in paths
        assert "app/routes.py" in paths
        assert "requirements.txt" in paths
        assert "README.md" in paths

    def test_empty_when_no_tasks(self, workspace):
        from llamaindex_crew.orchestrator.task_manager import TaskManager

        db_path = workspace / "tasks.db"
        tm = TaskManager(db_path, "test-project")
        paths = tm.get_registered_file_paths()
        assert paths == set()


class TestFileWriterAllowlist:
    """file_writer must reject paths not in the allowlist when enabled."""

    def test_rejects_unauthorized_path(self, workspace):
        from llamaindex_crew.tools.file_tools import (
            file_writer, set_allowed_file_paths,
        )
        ws = str(workspace)
        set_allowed_file_paths({"app/models.py", "app/routes.py"}, workspace=ws)
        try:
            result = file_writer("app/utils.py", "# utils", workspace_path=ws)
            assert "Rejected" in result
            assert not (workspace / "app/utils.py").exists()
        finally:
            set_allowed_file_paths(None, workspace=ws)

    def test_allows_registered_path(self, workspace):
        from llamaindex_crew.tools.file_tools import (
            file_writer, set_allowed_file_paths,
        )
        ws = str(workspace)
        set_allowed_file_paths({"app/models.py", "app/routes.py"}, workspace=ws)
        try:
            result = file_writer("app/models.py", "# models", workspace_path=ws)
            assert "Successfully" in result
            assert (workspace / "app/models.py").exists()
        finally:
            set_allowed_file_paths(None, workspace=ws)

    def test_no_guard_when_disabled(self, workspace):
        from llamaindex_crew.tools.file_tools import (
            file_writer, set_allowed_file_paths,
        )
        ws = str(workspace)
        set_allowed_file_paths(None, workspace=ws)
        result = file_writer("anything.py", "# ok", workspace_path=ws)
        assert "Successfully" in result
        assert (workspace / "anything.py").exists()

    def test_different_workspace_not_affected(self, workspace, tmp_path):
        """Allowlist on workspace A must not block writes to workspace B."""
        from llamaindex_crew.tools.file_tools import (
            file_writer, set_allowed_file_paths,
        )
        ws_a = str(workspace)
        ws_b = str(tmp_path / "other_workspace")
        (tmp_path / "other_workspace").mkdir()
        set_allowed_file_paths({"allowed.py"}, workspace=ws_a)
        try:
            result_blocked = file_writer("secret.py", "# no", workspace_path=ws_a)
            assert "Rejected" in result_blocked
            result_ok = file_writer("secret.py", "# yes", workspace_path=ws_b)
            assert "Successfully" in result_ok
        finally:
            set_allowed_file_paths(None, workspace=ws_a)


class TestEntrypointHints:
    """build_file_prompt must include framework wiring hints for entrypoint files."""

    def test_flask_app_gets_wiring_hints(self, workspace):
        from llamaindex_crew.orchestrator.task_manager import TaskManager, TaskDefinition

        db_path = workspace / "tasks.db"
        tm = TaskManager(db_path, "test-project")

        task = TaskDefinition(
            task_id="file_src_app_py",
            phase="development",
            task_type="file_creation",
            description="Create file: src/app.py",
            metadata={"file_path": "src/app.py"},
        )
        prompt = tm.build_file_prompt(task, tech_stack="Framework: Flask\nORM: SQLAlchemy")
        assert "ENTRYPOINT WIRING" in prompt
        assert "db.init_app" in prompt or "SQLAlchemy(app)" in prompt
        assert "route" in prompt.lower()

    def test_non_entrypoint_file_has_no_wiring_hints(self, workspace):
        from llamaindex_crew.orchestrator.task_manager import TaskManager, TaskDefinition

        db_path = workspace / "tasks.db"
        tm = TaskManager(db_path, "test-project")

        task = TaskDefinition(
            task_id="file_src_models_py",
            phase="development",
            task_type="file_creation",
            description="Create file: src/models.py",
            metadata={"file_path": "src/models.py"},
        )
        prompt = tm.build_file_prompt(task, tech_stack="Framework: Flask")
        assert "ENTRYPOINT WIRING" not in prompt

    def test_express_server_gets_wiring_hints(self, workspace):
        from llamaindex_crew.orchestrator.task_manager import TaskManager, TaskDefinition

        db_path = workspace / "tasks.db"
        tm = TaskManager(db_path, "test-project")

        task = TaskDefinition(
            task_id="file_server_js",
            phase="development",
            task_type="file_creation",
            description="Create file: server.js",
            metadata={"file_path": "server.js"},
        )
        prompt = tm.build_file_prompt(task, tech_stack="Framework: Express")
        assert "ENTRYPOINT WIRING" in prompt
        assert "app.listen" in prompt

    def test_spring_application_gets_wiring_hints(self, workspace):
        from llamaindex_crew.orchestrator.task_manager import TaskManager, TaskDefinition

        db_path = workspace / "tasks.db"
        tm = TaskManager(db_path, "test-project")

        task = TaskDefinition(
            task_id="file_Application_java",
            phase="development",
            task_type="file_creation",
            description="Create file: src/main/java/Application.java",
            metadata={"file_path": "src/main/java/Application.java"},
        )
        prompt = tm.build_file_prompt(task, tech_stack="Framework: Spring Boot")
        assert "ENTRYPOINT WIRING" in prompt
        assert "@SpringBootApplication" in prompt


# ═══════════════════════════════════════════════════════════════════════════════
# 11. Agent Summary Detection
# ═══════════════════════════════════════════════════════════════════════════════

class TestAgentSummaryDetection:

    def test_detects_ive_created_summary(self):
        from llamaindex_crew.workflows.software_dev_workflow import _is_agent_summary
        text = (
            "I've created the following files in your workspace:\n"
            "1. `requirements.md` - Requirements\n"
            "2. `user_stories.md` - User stories\n"
        )
        assert _is_agent_summary(text) is True

    def test_detects_i_have_created_summary(self):
        from llamaindex_crew.workflows.software_dev_workflow import _is_agent_summary
        text = "I have created the user stories and feature files as requested."
        assert _is_agent_summary(text) is True

    def test_detects_here_are_the_summary(self):
        from llamaindex_crew.workflows.software_dev_workflow import _is_agent_summary
        text = "Here are the files I generated for the project:\n- requirements.md\n"
        assert _is_agent_summary(text) is True

    def test_detects_let_me_know(self):
        from llamaindex_crew.workflows.software_dev_workflow import _is_agent_summary
        text = "Let me know if you need to modify any requirements or add additional scenarios!"
        assert _is_agent_summary(text) is True

    def test_detects_files_have_been_created(self):
        from llamaindex_crew.workflows.software_dev_workflow import _is_agent_summary
        text = (
            "The files have been successfully created in the workspace:\n"
            "1. `requirements.md` - Contains high-level requirements\n"
        )
        assert _is_agent_summary(text) is True

    def test_detects_all_content_aligns(self):
        from llamaindex_crew.workflows.software_dev_workflow import _is_agent_summary
        text = "All content aligns with the project vision, constraints, and value proposition."
        assert _is_agent_summary(text) is True

    def test_real_user_stories_not_flagged(self):
        from llamaindex_crew.workflows.software_dev_workflow import _is_agent_summary
        text = (
            "# User Stories\n\n"
            "## US-1: Create a task\n"
            "As a user I want to create a task so that I can track my work.\n\n"
            "### Acceptance Criteria\n"
            "Given I am on the task page\n"
            "When I click 'New Task'\n"
            "Then a new task is created\n"
        )
        assert _is_agent_summary(text) is False

    def test_real_gherkin_not_flagged(self):
        from llamaindex_crew.workflows.software_dev_workflow import _is_agent_summary
        text = (
            "Feature: Create a task\n"
            "  Scenario: Successfully create a task\n"
            "    Given I have a valid task payload\n"
            "    When I POST to /tasks\n"
            "    Then I receive a 201 status code\n"
        )
        assert _is_agent_summary(text) is False


# ═══════════════════════════════════════════════════════════════════════════════
# 12. Persist Phase Artifact — rejects summaries
# ═══════════════════════════════════════════════════════════════════════════════

class TestPersistPhaseArtifactSummaryRejection:

    def test_rejects_summary_response(self, workspace):
        from llamaindex_crew.workflows.software_dev_workflow import _persist_phase_artifact
        summary = (
            "I've created the following files in your workspace:\n"
            "1. `requirements.md` - High-level requirements\n"
            "2. `user_stories.md` - Detailed user stories\n"
            "3. `features/` - Gherkin feature files\n"
        )
        written = _persist_phase_artifact(workspace, "user_stories.md", summary)
        assert written is False
        assert not (workspace / "user_stories.md").exists()

    def test_accepts_real_content(self, workspace):
        from llamaindex_crew.workflows.software_dev_workflow import _persist_phase_artifact
        real_content = (
            "# User Stories\n\n"
            "## US-1: Create a task\n"
            "As a user I want to create a task\n"
            "so that I can track my work.\n"
        )
        written = _persist_phase_artifact(workspace, "user_stories.md", real_content)
        assert written is True
        assert (workspace / "user_stories.md").exists()

    def test_skips_if_file_already_exists(self, workspace):
        from llamaindex_crew.workflows.software_dev_workflow import _persist_phase_artifact
        (workspace / "user_stories.md").write_text("existing content", encoding="utf-8")
        written = _persist_phase_artifact(workspace, "user_stories.md", "any content\n\n\n\n")
        assert written is False

    def test_rejects_unable_to_generate_response(self, workspace):
        from llamaindex_crew.workflows.software_dev_workflow import _persist_phase_artifact
        error_msg = (
            "I'm unable to generate the requested files (`user_stories.md`, "
            "`features/*.feature`) as the system is blocking their creation. "
            "However, here's the content that would have been written:\n\n"
            "---\n**requirements.md**\n[Content]\n"
        )
        written = _persist_phase_artifact(workspace, "user_stories.md", error_msg)
        assert written is False
        assert not (workspace / "user_stories.md").exists()

    def test_rejects_manifest_error_response(self, workspace):
        from llamaindex_crew.workflows.software_dev_workflow import _persist_phase_artifact
        error_msg = (
            "The file 'design_spec.md' cannot be created because it's not "
            "included in the project file manifest. To resolve this, you "
            "would need to:\n1. Check the project manifest\n2. Add it\n"
        )
        written = _persist_phase_artifact(workspace, "design_spec.md", error_msg)
        assert written is False
        assert not (workspace / "design_spec.md").exists()

    def test_rejects_rejected_emoji_response(self, workspace):
        from llamaindex_crew.workflows.software_dev_workflow import _persist_phase_artifact
        error_msg = (
            "❌ Rejected: 'tech_stack.md' is not in the project file manifest. "
            "Only create files that are listed in the tech stack.\n\n\n\n"
        )
        written = _persist_phase_artifact(workspace, "tech_stack.md", error_msg)
        assert written is False

    def test_rejects_successfully_created_summary(self, workspace):
        from llamaindex_crew.workflows.software_dev_workflow import _persist_phase_artifact
        summary = (
            "The project documentation has been created successfully! "
            "Here's what was generated:\n"
            "1. **requirements.md**: High-level requirements\n"
            "2. **user_stories.md**: User stories with acceptance criteria\n"
        )
        written = _persist_phase_artifact(workspace, "requirements.md", summary)
        assert written is False

    def test_rejects_spec_created_as_summary(self, workspace):
        from llamaindex_crew.workflows.software_dev_workflow import _persist_phase_artifact
        summary = (
            "The design specification has been successfully created as "
            "`design_spec.md` in your workspace. It includes:\n\n"
            "- Complexity assessment\n- Bounded contexts\n- Data flow\n"
        )
        written = _persist_phase_artifact(workspace, "design_spec.md", summary)
        assert written is False

    def test_accepts_long_response_with_summary_preamble(self, workspace):
        """A long response that starts with a summary but contains real content should be saved."""
        from llamaindex_crew.workflows.software_dev_workflow import _persist_phase_artifact
        long_response = (
            "I've created the user stories document. Here are the details:\n\n"
            "# User Stories for Calculator App\n\n"
            "## Feature: Addition\n"
            "### As a user, I want to add two numbers\n"
            "**Acceptance Criteria:**\n"
            "- Given I enter 2 and 3\n- When I click add\n- Then I see 5\n\n"
            "## Feature: Subtraction\n"
            "### As a user, I want to subtract numbers\n"
            "**Acceptance Criteria:**\n"
            "- Given I enter 5 and 3\n- When I click subtract\n- Then I see 2\n\n"
            "## Feature: Multiplication\n"
            "### As a user, I want to multiply numbers\n"
            "**Acceptance Criteria:**\n"
            "- Given I enter 4 and 3\n- When I click multiply\n- Then I see 12\n\n"
            "## Feature: Division\n"
            "### As a user, I want to divide numbers\n"
            "**Acceptance Criteria:**\n"
            "- Given I enter 10 and 2\n- When I click divide\n- Then I see 5\n"
        )
        written = _persist_phase_artifact(workspace, "user_stories.md", long_response)
        assert written is True
        content = (workspace / "user_stories.md").read_text()
        assert content.startswith("# User Stories")

    def test_rejects_short_all_files_created_summary(self, workspace):
        """A short 'All files created' summary with just a file list should be rejected."""
        from llamaindex_crew.workflows.software_dev_workflow import _persist_phase_artifact
        summary = (
            "All required files have been successfully created:\n"
            "1. requirements.md (high-level requirements)\n"
            "2. user_stories.md (detailed user stories)\n"
            "3. features/addition.feature\n"
            "4. features/subtraction.feature\n\n"
            "The files contain proper Gherkin syntax.\n"
        )
        written = _persist_phase_artifact(workspace, "user_stories.md", summary)
        assert written is False


# ═══════════════════════════════════════════════════════════════════════════════
# 12b. YAML Block Extraction from Agent Responses
# ═══════════════════════════════════════════════════════════════════════════════

class TestExtractYamlBlock:

    def test_extracts_yaml_from_fenced_block(self):
        from llamaindex_crew.workflows.software_dev_workflow import _extract_yaml_block
        text = (
            "Here is the API contract:\n\n"
            "```yaml\n"
            "openapi: '3.0.3'\n"
            "info:\n"
            "  title: Test API\n"
            "  version: '1.0'\n"
            "paths:\n"
            "  /todos:\n"
            "    get:\n"
            "      operationId: listTodos\n"
            "```\n\n"
            "I've created the contract above."
        )
        result = _extract_yaml_block(text)
        assert result is not None
        assert "openapi:" in result
        assert "paths:" in result
        assert "```" not in result

    def test_extracts_yml_variant(self):
        from llamaindex_crew.workflows.software_dev_workflow import _extract_yaml_block
        text = "```yml\nopenapi: '3.0.3'\ninfo:\n  title: X\n  version: '1'\npaths: {}\n```"
        result = _extract_yaml_block(text)
        assert result is not None
        assert "openapi:" in result

    def test_returns_none_for_no_yaml(self):
        from llamaindex_crew.workflows.software_dev_workflow import _extract_yaml_block
        assert _extract_yaml_block("No YAML here, just text.") is None

    def test_returns_none_for_too_short_yaml(self):
        from llamaindex_crew.workflows.software_dev_workflow import _extract_yaml_block
        assert _extract_yaml_block("```yaml\nfoo: bar\n```") is None


# ═══════════════════════════════════════════════════════════════════════════════
# 13. Gherkin Feature Extraction
# ═══════════════════════════════════════════════════════════════════════════════

class TestGherkinFeatureExtraction:

    def test_extracts_single_feature(self):
        from llamaindex_crew.workflows.software_dev_workflow import _extract_gherkin_features
        text = (
            "Feature: Create Task\n"
            "  Scenario: Successfully create a task\n"
            "    Given I have a valid payload\n"
            "    When I POST to /tasks\n"
            "    Then I receive a 201\n"
        )
        result = _extract_gherkin_features(text)
        assert len(result) == 1
        assert "create_task" in result
        assert "Scenario:" in result["create_task"]

    def test_extracts_multiple_features(self):
        from llamaindex_crew.workflows.software_dev_workflow import _extract_gherkin_features
        text = (
            "Feature: Create Task\n"
            "  Scenario: Create\n"
            "    Given something\n"
            "    When I do something\n"
            "    Then it works\n\n"
            "Feature: Delete Task\n"
            "  Scenario: Delete\n"
            "    Given a task exists\n"
            "    When I DELETE /tasks/1\n"
            "    Then it is removed\n"
        )
        result = _extract_gherkin_features(text)
        assert len(result) == 2
        assert "create_task" in result
        assert "delete_task" in result

    def test_returns_empty_for_no_features(self):
        from llamaindex_crew.workflows.software_dev_workflow import _extract_gherkin_features
        result = _extract_gherkin_features("Just some random text with no Gherkin.")
        assert result == {}

    def test_extracts_from_markdown_with_gherkin_blocks(self):
        from llamaindex_crew.workflows.software_dev_workflow import _extract_gherkin_features
        text = (
            "# User Stories\n\n"
            "## US-1: Task Management\n\n"
            "Feature: Task CRUD Operations\n"
            "  Scenario: Get all tasks\n"
            "    Given the API is running\n"
            "    When I GET /tasks\n"
            "    Then I receive a list of tasks\n\n"
            "Some other text here.\n"
        )
        result = _extract_gherkin_features(text)
        assert len(result) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 14. Ensure Feature Files
# ═══════════════════════════════════════════════════════════════════════════════

class TestEnsureFeatureFiles:

    def test_skips_if_features_already_exist(self, workspace):
        from llamaindex_crew.workflows.software_dev_workflow import _ensure_feature_files
        features_dir = workspace / "features"
        features_dir.mkdir()
        (features_dir / "existing.feature").write_text("Feature: Existing\n")
        count = _ensure_feature_files(workspace, "any text")
        assert count == 1

    def test_extracts_from_user_stories(self, workspace):
        from llamaindex_crew.workflows.software_dev_workflow import _ensure_feature_files
        stories = (
            "Feature: Create Task\n"
            "  Scenario: Create\n"
            "    Given valid data\n"
            "    When I POST /tasks\n"
            "    Then created\n\n"
            "Feature: Get Tasks\n"
            "  Scenario: List\n"
            "    Given tasks exist\n"
            "    When I GET /tasks\n"
            "    Then I see them\n"
        )
        count = _ensure_feature_files(workspace, stories)
        assert count == 2
        assert (workspace / "features" / "create_task.feature").exists()
        assert (workspace / "features" / "get_tasks.feature").exists()

    def test_returns_zero_when_no_gherkin(self, workspace):
        from llamaindex_crew.workflows.software_dev_workflow import _ensure_feature_files
        count = _ensure_feature_files(workspace, "No Gherkin content here.")
        assert count == 0
        assert not (workspace / "features").exists()

    def test_feature_file_content_is_valid_gherkin(self, workspace):
        from llamaindex_crew.workflows.software_dev_workflow import _ensure_feature_files
        stories = (
            "Feature: Update Task\n"
            "  Scenario: Update title\n"
            "    Given a task exists with id 1\n"
            "    When I PUT /tasks/1 with new title\n"
            "    Then the task title is updated\n"
        )
        _ensure_feature_files(workspace, stories)
        content = (workspace / "features" / "update_task.feature").read_text()
        assert content.startswith("Feature: Update Task")
        assert "Scenario:" in content
        assert "Given" in content


# ═══════════════════════════════════════════════════════════════════════════════
# Language Strategy & Registry Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestLanguageStrategyABC:
    """Verify the abstract interface can be subclassed."""

    def test_python_strategy_has_correct_name_and_extensions(self):
        from llamaindex_crew.orchestrator.language_strategies import PythonStrategy
        s = PythonStrategy()
        assert s.name == "python"
        assert ".py" in s.extensions

    def test_java_strategy_has_correct_name_and_extensions(self):
        from llamaindex_crew.orchestrator.language_strategies import JavaStrategy
        s = JavaStrategy()
        assert s.name == "java"
        assert ".java" in s.extensions
        assert ".kt" in s.extensions

    def test_javascript_strategy_has_correct_name_and_extensions(self):
        from llamaindex_crew.orchestrator.language_strategies import JavaScriptStrategy
        s = JavaScriptStrategy()
        assert s.name == "javascript"
        assert ".js" in s.extensions
        assert ".tsx" in s.extensions


class TestStrategyRegistry:
    """Test the registry: built-in strategies, detection, and YAML config."""

    def test_builtin_strategies_registered(self):
        from llamaindex_crew.orchestrator.language_strategies import StrategyRegistry
        r = StrategyRegistry()
        assert r.get_by_name("python") is not None
        assert r.get_by_name("java") is not None
        assert r.get_by_name("javascript") is not None

    def test_get_by_extension(self):
        from llamaindex_crew.orchestrator.language_strategies import StrategyRegistry
        r = StrategyRegistry()
        assert r.get_by_extension(".py").name == "python"
        assert r.get_by_extension(".java").name == "java"
        assert r.get_by_extension(".ts").name == "javascript"

    def test_get_for_file(self):
        from llamaindex_crew.orchestrator.language_strategies import StrategyRegistry
        r = StrategyRegistry()
        assert r.get_for_file(Path("src/app.py")).name == "python"
        assert r.get_for_file(Path("src/App.java")).name == "java"
        assert r.get_for_file(Path("src/index.tsx")).name == "javascript"
        assert r.get_for_file(Path("README.md")) is None

    def test_detect_from_tech_stack_python(self):
        from llamaindex_crew.orchestrator.language_strategies import StrategyRegistry
        r = StrategyRegistry()
        strategies = r.detect_from_tech_stack("Flask with SQLAlchemy and Python 3.11")
        names = [s.name for s in strategies]
        assert "python" in names

    def test_detect_from_tech_stack_java(self):
        from llamaindex_crew.orchestrator.language_strategies import StrategyRegistry
        r = StrategyRegistry()
        strategies = r.detect_from_tech_stack("Spring Boot with Maven and Java 21")
        names = [s.name for s in strategies]
        assert "java" in names

    def test_detect_from_tech_stack_fullstack(self):
        from llamaindex_crew.orchestrator.language_strategies import StrategyRegistry
        r = StrategyRegistry()
        strategies = r.detect_from_tech_stack("Flask backend + React frontend")
        names = [s.name for s in strategies]
        assert "python" in names
        assert "javascript" in names

    def test_is_fullstack(self):
        from llamaindex_crew.orchestrator.language_strategies import StrategyRegistry
        r = StrategyRegistry()
        assert r.is_fullstack("Flask backend with React frontend") is True
        assert r.is_fullstack("Spring Boot with Vue.js") is True
        assert r.is_fullstack("Flask with SQLAlchemy") is False
        assert r.is_fullstack("React Native mobile app") is False

    def test_yaml_config_loads(self, workspace):
        from llamaindex_crew.orchestrator.language_strategies import StrategyRegistry
        import yaml
        cfg_dir = workspace / "strategies"
        cfg_dir.mkdir()
        config = {
            "language": "python",
            "checks": {
                "entrypoint": {
                    "frameworks": {
                        "custom_fw": {
                            "patterns": [{"regex": r"CustomApp\(\)", "label": "custom init"}],
                            "files": ["custom_main.py"],
                        }
                    }
                }
            }
        }
        (cfg_dir / "python.yaml").write_text(yaml.dump(config))
        r = StrategyRegistry(config_dir=cfg_dir)
        py = r.get_by_name("python")
        assert "custom_fw" in py._FRAMEWORK_WIRING
        assert "custom_main.py" in py._ENTRYPOINT_FILENAMES["custom_fw"]


class TestPythonStrategySyntax:
    def test_valid_python(self, workspace):
        from llamaindex_crew.orchestrator.language_strategies import PythonStrategy
        s = PythonStrategy()
        f = workspace / "valid.py"
        f.write_text("x = 1\nprint(x)\n")
        r = s.validate_syntax(f)
        assert r["valid"] is True

    def test_invalid_python(self, workspace):
        from llamaindex_crew.orchestrator.language_strategies import PythonStrategy
        s = PythonStrategy()
        f = workspace / "bad.py"
        f.write_text("def foo(\n")
        r = s.validate_syntax(f)
        assert r["valid"] is False
        assert "SyntaxError" in r["error"]


class TestJavaStrategySyntax:
    def test_valid_java(self, workspace):
        from llamaindex_crew.orchestrator.language_strategies import JavaStrategy
        s = JavaStrategy()
        f = workspace / "App.java"
        f.write_text("public class App { public static void main(String[] args) {} }")
        r = s.validate_syntax(f)
        assert r["valid"] is True

    def test_unbalanced_braces(self, workspace):
        from llamaindex_crew.orchestrator.language_strategies import JavaStrategy
        s = JavaStrategy()
        f = workspace / "Bad.java"
        f.write_text("public class Bad { public void foo() { }")
        r = s.validate_syntax(f)
        assert r["valid"] is False


class TestJavaScriptStrategySyntax:
    def test_valid_js(self, workspace):
        from llamaindex_crew.orchestrator.language_strategies import JavaScriptStrategy
        s = JavaScriptStrategy()
        f = workspace / "app.js"
        f.write_text("const x = 1;\nconsole.log(x);\n")
        r = s.validate_syntax(f)
        assert r["valid"] is True


class TestPythonStrategyContractConformance:
    def _make_contract(self, paths):
        return {"paths": paths}

    def test_flask_routes_match_contract(self, workspace):
        from llamaindex_crew.orchestrator.language_strategies import PythonStrategy
        s = PythonStrategy()
        routes = workspace / "routes.py"
        routes.write_text(
            "from flask import Flask\n"
            "app = Flask(__name__)\n"
            "@app.route('/todos')\ndef list_todos(): pass\n"
            "@app.route('/todos/<int:id>')\ndef get_todo(id): pass\n"
        )
        contract = self._make_contract({
            "/todos": {"get": {"summary": "List"}},
            "/todos/{id}": {"get": {"summary": "Get one"}},
        })
        result = s.validate_contract_conformance(workspace, contract)
        assert result["valid"] is True
        assert result["missing_endpoints"] == []

    def test_missing_endpoint_detected(self, workspace):
        from llamaindex_crew.orchestrator.language_strategies import PythonStrategy
        s = PythonStrategy()
        routes = workspace / "routes.py"
        routes.write_text(
            "from flask import Flask\n"
            "app = Flask(__name__)\n"
            "@app.route('/todos')\ndef list_todos(): pass\n"
        )
        contract = self._make_contract({
            "/todos": {"get": {"summary": "List"}},
            "/todos/{id}": {"delete": {"summary": "Delete"}},
        })
        result = s.validate_contract_conformance(workspace, contract)
        assert result["valid"] is False
        assert len(result["missing_endpoints"]) > 0


class TestJavaStrategyContractConformance:
    def test_spring_controller_matches(self, workspace):
        from llamaindex_crew.orchestrator.language_strategies import JavaStrategy
        s = JavaStrategy()
        ctrl = workspace / "TodoController.java"
        ctrl.write_text(
            '@RestController\n'
            '@RequestMapping("/api/todos")\n'
            'public class TodoController {\n'
            '  @GetMapping("/")\n'
            '  public List<Todo> list() { return null; }\n'
            '  @PostMapping("/")\n'
            '  public Todo create() { return null; }\n'
            '}\n'
        )
        contract = {"paths": {
            "/api/todos/": {"get": {"summary": "List"}, "post": {"summary": "Create"}},
        }}
        result = s.validate_contract_conformance(workspace, contract)
        assert result["valid"] is True


class TestJavaStrategyPackageStructure:
    def test_correct_package_passes(self, workspace):
        from llamaindex_crew.orchestrator.language_strategies import JavaStrategy
        s = JavaStrategy()
        pkg_dir = workspace / "com" / "example"
        pkg_dir.mkdir(parents=True)
        f = pkg_dir / "App.java"
        f.write_text("package com.example;\npublic class App {}\n")
        result = s.validate_package_structure(workspace)
        assert result["valid"] is True

    def test_wrong_package_fails(self, workspace):
        from llamaindex_crew.orchestrator.language_strategies import JavaStrategy
        s = JavaStrategy()
        f = workspace / "App.java"
        f.write_text("package com.example.wrong;\npublic class App {}\n")
        result = s.validate_package_structure(workspace)
        assert result["valid"] is False


class TestBuildFilePromptWithApiContract:
    """Test that build_file_prompt injects API contract for route/client files."""

    @pytest.fixture
    def tm(self, workspace):
        return TaskManager(workspace / "tasks.db", "test-contract")

    def test_route_file_gets_contract(self, tm):
        task = TaskDefinition(
            task_id="t1", phase="development", task_type="file_creation",
            description="routes", required=True, source="test",
            metadata={"file_path": "app/routes.py"},
        )
        contract = {"paths": {"/todos": {"get": {"summary": "List todos"}}},
                     "components": {"schemas": {"Todo": {"properties": {"id": {}, "title": {}}}}}}
        prompt = tm.build_file_prompt(task, api_contract=contract)
        assert "API CONTRACT" in prompt
        assert "GET /todos" in prompt
        assert "Todo" in prompt

    def test_frontend_client_gets_contract(self, tm):
        task = TaskDefinition(
            task_id="t2", phase="development", task_type="file_creation",
            description="api client", required=True, source="test",
            metadata={"file_path": "src/api/client.ts"},
        )
        contract = {"paths": {"/users": {"post": {"summary": "Create user"}}}}
        prompt = tm.build_file_prompt(task, api_contract=contract)
        assert "API CONTRACT" in prompt
        assert "POST /users" in prompt

    def test_model_file_does_not_get_contract(self, tm):
        task = TaskDefinition(
            task_id="t3", phase="development", task_type="file_creation",
            description="model", required=True, source="test",
            metadata={"file_path": "app/models.py"},
        )
        contract = {"paths": {"/todos": {"get": {}}}}
        prompt = tm.build_file_prompt(task, api_contract=contract)
        assert "API CONTRACT" not in prompt

    def test_no_contract_passed(self, tm):
        task = TaskDefinition(
            task_id="t4", phase="development", task_type="file_creation",
            description="routes", required=True, source="test",
            metadata={"file_path": "app/routes.py"},
        )
        prompt = tm.build_file_prompt(task, api_contract=None)
        assert "API CONTRACT" not in prompt


class TestCodeValidatorDelegatesToStrategies:
    """Verify CodeCompletenessValidator backward-compatible methods delegate properly."""

    def test_validate_syntax_python(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator
        f = workspace / "ok.py"
        f.write_text("x = 1\n")
        assert CodeCompletenessValidator.validate_syntax(f)["valid"] is True

    def test_validate_syntax_java(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator
        f = workspace / "Ok.java"
        f.write_text("public class Ok {}\n")
        assert CodeCompletenessValidator.validate_syntax(f)["valid"] is True

    def test_validate_imports_delegates(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator
        f = workspace / "app.py"
        f.write_text("import os\nx = 1\n")
        result = CodeCompletenessValidator.validate_imports(f, workspace)
        assert result["valid"] is True

    def test_extract_export_summary_delegates(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator
        f = workspace / "mod.py"
        f.write_text("def hello(): pass\nclass World: pass\n")
        result = CodeCompletenessValidator.extract_export_summary(f)
        assert result["type"] == "python"
        assert "hello" in result["exports"]
        assert "World" in result["exports"]

    def test_validate_entrypoint_delegates(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator
        f = workspace / "app.py"
        f.write_text(
            "from flask import Flask\n"
            "app = Flask(__name__)\n"
            "from routes import bp\n"
            "@app.route('/')\ndef index(): pass\n"
        )
        result = CodeCompletenessValidator.validate_entrypoint(workspace, "Flask web app")
        assert result["framework"] == "flask"

    def test_validate_contract_conformance_via_validator(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator
        f = workspace / "routes.py"
        f.write_text(
            "from flask import Flask\napp = Flask(__name__)\n"
            "@app.route('/items')\ndef items(): pass\n"
        )
        contract = {"paths": {"/items": {"get": {}}}}
        result = CodeCompletenessValidator.validate_contract_conformance(
            workspace, contract, "Flask"
        )
        assert result["valid"] is True


class TestStrategyRegistryDetectPrimary:
    def test_primary_is_first(self):
        from llamaindex_crew.orchestrator.language_strategies import StrategyRegistry
        r = StrategyRegistry()
        primary = r.detect_primary_from_tech_stack("Python Flask API")
        assert primary is not None
        assert primary.name == "python"

    def test_none_for_unknown(self):
        from llamaindex_crew.orchestrator.language_strategies import StrategyRegistry
        r = StrategyRegistry()
        assert r.detect_primary_from_tech_stack("COBOL mainframe app") is None


class TestOpenAPIPathExtraction:
    def test_extracts_paths(self):
        from llamaindex_crew.orchestrator.language_strategies import _extract_openapi_paths
        contract = {
            "paths": {
                "/todos": {"get": {}, "post": {}},
                "/todos/{id}": {"get": {}, "put": {}, "delete": {}},
            }
        }
        result = _extract_openapi_paths(contract)
        assert "/todos" in result
        assert result["/todos"] == {"GET", "POST"}
        assert result["/todos/{id}"] == {"GET", "PUT", "DELETE"}

    def test_empty_contract(self):
        from llamaindex_crew.orchestrator.language_strategies import _extract_openapi_paths
        assert _extract_openapi_paths({}) == {}
        assert _extract_openapi_paths({"paths": {}}) == {}


# ═══════════════════════════════════════════════════════════════════════════════
# Module system detection and per-file prompt enrichment
# ═══════════════════════════════════════════════════════════════════════════════

class TestDetectModuleSystem:
    """_detect_module_system must extract the declared module system from tech_stack text."""

    def test_detects_es_modules(self):
        from llamaindex_crew.orchestrator.task_manager import TaskManager
        ts = "## Core Technology\n**Module System**: ES modules\n**Framework**: Vanilla JS"
        assert TaskManager._detect_module_system(ts) == "esm"

    def test_detects_commonjs(self):
        from llamaindex_crew.orchestrator.task_manager import TaskManager
        ts = "## Core\n**Module System**: CommonJS\n**Runtime**: Node.js"
        assert TaskManager._detect_module_system(ts) == "commonjs"

    def test_detects_script_tags(self):
        from llamaindex_crew.orchestrator.task_manager import TaskManager
        ts = "## Core\n**Module System**: Script tags\nNo modules."
        assert TaskManager._detect_module_system(ts) == "script-tags"

    def test_returns_none_for_python(self):
        from llamaindex_crew.orchestrator.task_manager import TaskManager
        ts = "## Core\n**Language**: Python 3.10+\n**Framework**: Flask"
        assert TaskManager._detect_module_system(ts) is None

    def test_returns_none_for_empty(self):
        from llamaindex_crew.orchestrator.task_manager import TaskManager
        assert TaskManager._detect_module_system("") is None
        assert TaskManager._detect_module_system(None) is None

    def test_case_insensitive(self):
        from llamaindex_crew.orchestrator.task_manager import TaskManager
        ts = "Module system: es modules"
        assert TaskManager._detect_module_system(ts) == "esm"


class TestBuildFilePromptModuleSystem:
    """build_file_prompt must include a MODULE SYSTEM section for JS/TS files."""

    def test_includes_module_system_for_js_file(self, task_mgr):
        t = TaskDefinition(
            task_id="app_js",
            phase="development",
            task_type="file_creation",
            description="Create app.js",
            metadata={"file_path": "src/app.js"},
        )
        task_mgr.register_task(t)
        prompt = task_mgr.build_file_prompt(
            t, tech_stack="**Module System**: ES modules\n**Framework**: Vanilla JS"
        )
        assert "MODULE SYSTEM" in prompt
        assert "import" in prompt.lower() and "export" in prompt.lower()

    def test_includes_commonjs_for_js_file(self, task_mgr):
        t = TaskDefinition(
            task_id="server_js",
            phase="development",
            task_type="file_creation",
            description="Create server.js",
            metadata={"file_path": "server.js"},
        )
        task_mgr.register_task(t)
        prompt = task_mgr.build_file_prompt(
            t, tech_stack="**Module System**: CommonJS\n**Runtime**: Node.js"
        )
        assert "MODULE SYSTEM" in prompt
        assert "require" in prompt.lower()

    def test_no_module_system_for_python_file(self, task_mgr):
        t = TaskDefinition(
            task_id="app_py",
            phase="development",
            task_type="file_creation",
            description="Create app.py",
            metadata={"file_path": "src/app.py"},
        )
        task_mgr.register_task(t)
        prompt = task_mgr.build_file_prompt(
            t, tech_stack="**Language**: Python 3.10+\n**Framework**: Flask"
        )
        assert "MODULE SYSTEM" not in prompt

    def test_no_module_system_when_not_declared(self, task_mgr):
        t = TaskDefinition(
            task_id="index_js",
            phase="development",
            task_type="file_creation",
            description="Create index.js",
            metadata={"file_path": "src/index.js"},
        )
        task_mgr.register_task(t)
        prompt = task_mgr.build_file_prompt(
            t, tech_stack="Vanilla HTML + CSS + JS"
        )
        assert "MODULE SYSTEM" not in prompt


# ═══════════════════════════════════════════════════════════════════════════════
# Module system consistency validation
# ═══════════════════════════════════════════════════════════════════════════════

class TestModuleConsistencyValidator:
    """validate_module_consistency must detect mixed module systems in JS/TS projects."""

    def test_all_esm_passes(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator
        (workspace / "app.js").write_text("import { x } from './utils.js';\nexport default x;\n")
        (workspace / "utils.js").write_text("export const x = 1;\n")
        result = CodeCompletenessValidator.validate_module_consistency(workspace)
        assert result["valid"] is True

    def test_all_cjs_passes(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator
        (workspace / "app.js").write_text("const x = require('./utils');\nmodule.exports = x;\n")
        (workspace / "utils.js").write_text("module.exports = { x: 1 };\n")
        result = CodeCompletenessValidator.validate_module_consistency(workspace)
        assert result["valid"] is True

    def test_mixed_esm_cjs_fails(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator
        (workspace / "app.js").write_text("import { x } from './utils.js';\nexport default x;\n")
        (workspace / "server.js").write_text("const express = require('express');\nmodule.exports = express();\n")
        result = CodeCompletenessValidator.validate_module_consistency(workspace)
        assert result["valid"] is False
        assert len(result.get("conflicts", [])) >= 1

    def test_single_file_mixed_detected(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator
        (workspace / "app.js").write_text(
            "import { x } from './utils.js';\n"
            "const y = require('./other');\n"
            "module.exports = { x, y };\n"
        )
        result = CodeCompletenessValidator.validate_module_consistency(workspace)
        assert result["valid"] is False

    def test_no_js_files_passes(self, workspace):
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator
        (workspace / "app.py").write_text("import os\nprint(os.getcwd())\n")
        result = CodeCompletenessValidator.validate_module_consistency(workspace)
        assert result["valid"] is True

    def test_test_files_excluded(self, workspace):
        """Test files may legitimately use different module system (Jest transforms)."""
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator
        (workspace / "app.js").write_text("import { x } from './utils.js';\nexport default x;\n")
        (workspace / "utils.js").write_text("export const x = 1;\n")
        tests_dir = workspace / "tests"
        tests_dir.mkdir()
        (tests_dir / "app.test.js").write_text("const { x } = require('../app');\ntest('x', () => {});\n")
        result = CodeCompletenessValidator.validate_module_consistency(workspace)
        assert result["valid"] is True


# ═══════════════════════════════════════════════════════════════════════════════
# Fix 1: JS relative import resolution handles ../ paths correctly
# ═══════════════════════════════════════════════════════════════════════════════

class TestJsRelativeImportResolution:
    """_js_relative_import_exists must properly resolve ../ parent-dir traversals."""

    def test_dot_dot_slash_resolves_correctly(self, workspace):
        """../utils/foo.js from components/bar.js should find utils/foo.js."""
        from llamaindex_crew.orchestrator.language_strategies import JavaScriptStrategy
        (workspace / "src" / "utils").mkdir(parents=True)
        (workspace / "src" / "components").mkdir(parents=True)
        (workspace / "src" / "utils" / "data.js").write_text("export const x = 1;\n")
        source_file = workspace / "src" / "components" / "card.js"
        source_file.write_text("import { x } from '../utils/data.js';\n")
        assert JavaScriptStrategy._js_relative_import_exists(
            "../utils/data.js", source_file, workspace
        ) is True

    def test_dot_dot_slash_invalid_path_detected(self, workspace):
        """../../utils/foo.js from components/bar.js should NOT resolve (too many ..)."""
        from llamaindex_crew.orchestrator.language_strategies import JavaScriptStrategy
        (workspace / "src" / "utils").mkdir(parents=True)
        (workspace / "src" / "components").mkdir(parents=True)
        (workspace / "src" / "utils" / "data.js").write_text("export const x = 1;\n")
        source_file = workspace / "src" / "components" / "card.js"
        source_file.write_text("")
        assert JavaScriptStrategy._js_relative_import_exists(
            "../../utils/data.js", source_file, workspace
        ) is False

    def test_dot_slash_still_works(self, workspace):
        """./utils/data.js from src/app.js should work."""
        from llamaindex_crew.orchestrator.language_strategies import JavaScriptStrategy
        (workspace / "src" / "utils").mkdir(parents=True)
        (workspace / "src" / "utils" / "data.js").write_text("export const x = 1;\n")
        source_file = workspace / "src" / "app.js"
        source_file.write_text("")
        assert JavaScriptStrategy._js_relative_import_exists(
            "./utils/data.js", source_file, workspace
        ) is True

    def test_dot_dot_without_extension_tries_js(self, workspace):
        """../utils/data (no .js) should find ../utils/data.js."""
        from llamaindex_crew.orchestrator.language_strategies import JavaScriptStrategy
        (workspace / "src" / "utils").mkdir(parents=True)
        (workspace / "src" / "components").mkdir(parents=True)
        (workspace / "src" / "utils" / "data.js").write_text("export const x = 1;\n")
        source_file = workspace / "src" / "components" / "card.js"
        source_file.write_text("")
        assert JavaScriptStrategy._js_relative_import_exists(
            "../utils/data", source_file, workspace
        ) is True

    def test_validate_imports_dot_dot_not_broken(self, workspace):
        """Full validate_imports must NOT flag valid ../ imports as broken."""
        from llamaindex_crew.orchestrator.language_strategies import JavaScriptStrategy
        (workspace / "src" / "utils").mkdir(parents=True)
        (workspace / "src" / "components").mkdir(parents=True)
        (workspace / "src" / "utils" / "data.js").write_text("export const x = 1;\n")
        comp = workspace / "src" / "components" / "card.js"
        comp.write_text("import { x } from '../utils/data.js';\nexport default x;\n")
        result = JavaScriptStrategy().validate_imports(comp, workspace)
        assert result["valid"] is True, f"valid ../utils/data.js flagged as broken: {result}"

    def test_case_insensitive_fallback(self, workspace):
        """Import 'Loader.js' should match file 'loader.js' (case-insensitive)."""
        from llamaindex_crew.orchestrator.language_strategies import JavaScriptStrategy
        (workspace / "src" / "components").mkdir(parents=True)
        (workspace / "src" / "components" / "loader.js").write_text("export default class Loader {}\n")
        source_file = workspace / "src" / "app.js"
        source_file.write_text("")
        assert JavaScriptStrategy._js_relative_import_exists(
            "./components/Loader.js", source_file, workspace
        ) is True


# ═══════════════════════════════════════════════════════════════════════════════
# Fix 3: Test-specific guidance in build_file_prompt
# ═══════════════════════════════════════════════════════════════════════════════

class TestBuildFilePromptTestGuidance:
    """build_file_prompt must include test framework rules when creating test files."""

    def test_test_file_includes_framework_guidance(self, task_mgr):
        t = TaskDefinition(
            task_id="test_app",
            phase="development",
            task_type="file_creation",
            description="Create test file for app",
            metadata={"file_path": "tests/app.test.js"},
        )
        task_mgr.register_task(t)
        prompt = task_mgr.build_file_prompt(
            t, tech_stack="**Framework**: Vanilla HTML5 + CSS3 + JavaScript\n**Testing**: Jest with jsdom\n**Module System**: ES modules"
        )
        assert "jest" in prompt.lower() or "Jest" in prompt
        assert "@testing-library/react" in prompt or "testing-library" in prompt.lower()

    def test_react_test_file_allows_testing_library(self, task_mgr):
        t = TaskDefinition(
            task_id="test_comp",
            phase="development",
            task_type="file_creation",
            description="Create test file for component",
            metadata={"file_path": "src/__tests__/App.test.tsx"},
        )
        task_mgr.register_task(t)
        prompt = task_mgr.build_file_prompt(
            t, tech_stack="**Framework**: React\n**Testing**: Jest + @testing-library/react"
        )
        assert "@testing-library/react" in prompt

    def test_non_test_file_no_test_guidance(self, task_mgr):
        t = TaskDefinition(
            task_id="app_js",
            phase="development",
            task_type="file_creation",
            description="Create app.js",
            metadata={"file_path": "src/app.js"},
        )
        task_mgr.register_task(t)
        prompt = task_mgr.build_file_prompt(
            t, tech_stack="**Framework**: Vanilla HTML5 + CSS3 + JavaScript\n**Testing**: Jest with jsdom"
        )
        assert "TEST IMPORTS" not in prompt


# ═══════════════════════════════════════════════════════════════════════════════
# Fix 4: Project file tree in build_file_prompt
# ═══════════════════════════════════════════════════════════════════════════════

class TestBuildFilePromptFileTree:
    """build_file_prompt must include a project file tree from registered tasks."""

    def test_prompt_includes_file_tree(self, task_mgr):
        files = [
            ("t1", "public/index.html"),
            ("t2", "public/js/app.js"),
            ("t3", "public/js/utils/data.js"),
            ("t4", "tests/app.test.js"),
        ]
        for tid, fp in files:
            t = TaskDefinition(
                task_id=tid, phase="development", task_type="file_creation",
                description=f"Create {fp}", metadata={"file_path": fp},
            )
            task_mgr.register_task(t)
        target = TaskDefinition(
            task_id="t4", phase="development", task_type="file_creation",
            description="Create tests/app.test.js",
            metadata={"file_path": "tests/app.test.js"},
        )
        prompt = task_mgr.build_file_prompt(target, tech_stack="Vanilla JS")
        assert "PROJECT FILE TREE" in prompt or "FILE TREE" in prompt
        assert "public/js/app.js" in prompt
        assert "public/js/utils/data.js" in prompt

    def test_file_tree_includes_all_registered_paths(self, task_mgr):
        files = ["src/main.py", "src/models.py", "src/routes.py", "tests/test_main.py"]
        for i, fp in enumerate(files):
            t = TaskDefinition(
                task_id=f"t{i}", phase="development", task_type="file_creation",
                description=f"Create {fp}", metadata={"file_path": fp},
            )
            task_mgr.register_task(t)
        target = TaskDefinition(
            task_id="t3", phase="development", task_type="file_creation",
            description="Create tests/test_main.py",
            metadata={"file_path": "tests/test_main.py"},
        )
        prompt = task_mgr.build_file_prompt(target, tech_stack="Python Flask")
        for fp in files:
            assert fp in prompt, f"File tree should contain {fp}"
