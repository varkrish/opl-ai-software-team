
# AI Software Development Crew - Implementation Guide

## System Overview

A scalable, multi-agent AI software development team that follows BDD (Gherkin) and TDD practices, with budget control and manager coordination. Built with CrewAI and deployable on Kubernetes.

---

## Architecture

### CrewAI Crew Hierarchy

```
┌─────────────────────────────────────────────────────────┐
│                  Budget Controller                       │
│         (Tracks costs via callbacks, blocks if exceeded) │
└─────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────┐
│                    BA Crew (Sequential)                  │
│  BA Agent → Requirements → Gherkin Scenarios → Sign-off  │
└─────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────┐
│            Engineering Manager Crew (Hierarchical)       │
│     Manager Agent delegates to Dev/QA/DevOps Managers    │
└─────────────────────────────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        │                   │                   │
        ▼                   ▼                   ▼
┌──────────────┐  ┌──────────────┐  ┌────────────────┐
│  Dev Crew    │  │   QA Crew    │  │  DevOps Crew   │
│(Hierarchical)│  │ (Sequential) │  │  (Sequential)  │
└──────────────┘  └──────────────┘  └────────────────┘
   │                    │                   │
   ▼                    ▼                   ▼
Backend/Frontend    BDD/Unit/E2E       Docker/K8s/CI
Developers          Test Agents         Agents
```

### CrewAI Process Types

- **BA Crew:** `Process.sequential` - Requirements → Analysis → Sign-off
- **Engineering Manager Crew:** `Process.hierarchical` - Manager delegates to sub-managers
- **Dev Crew:** `Process.hierarchical` - Dev Manager assigns to Backend/Frontend devs
- **QA Crew:** `Process.sequential` - Unit → Integration → E2E tests
- **DevOps Crew:** `Process.sequential` - Build → Test → Deploy pipeline

---

## Agent Roles

### 1. **Budget Agent** (Guardian)

- **Role:** Cost controller and gatekeeper
- **Responsibilities:**
  - Track AI token usage across all agents
  - Monitor cost per project/task
  - Block agent requests if budget exceeded
  - Generate cost reports
  - Alert managers on high spending
- **Scaling:** 1 replica (singleton)
- **Priority:** Critical (always runs first)

### 2. **Business Analyst (BA)** (Final Authority)

- **Role:** Requirements owner and sign-off authority
- **Responsibilities:**
  - Analyze user vision and create detailed requirements
  - Define acceptance criteria (Gherkin scenarios)
  - Review completed work for sign-off
  - Reject work that doesn't meet criteria
  - Create project scope document
- **Scaling:** 1-2 replicas
- **BDD Output:** Feature files with Gherkin scenarios

### 3. **Engineering Manager** (Orchestrator)

- **Role:** Overall development coordinator
- **Responsibilities:**
  - Break down requirements into tasks
  - Assign tasks to dev/qa/ops managers
  - Track overall progress
  - Resolve conflicts between teams
  - Report to BA
- **Scaling:** 1 replica

### 4. **Development Manager**

- **Role:** Code development coordinator
- **Responsibilities:**
  - Assign coding tasks to developers
  - Review code quality
  - Ensure TDD practices followed
  - Manage technical debt
  - Coordinate with QA Manager
- **Scaling:** 1-2 replicas

### 5. **QA Manager**

- **Role:** Testing and quality coordinator
- **Responsibilities:**
  - Create test strategy from Gherkin scenarios
  - Assign testing tasks
  - Report bugs to Dev Manager
  - Verify fixes
  - Sign-off on test completion
- **Scaling:** 1 replica

### 6. **DevOps Manager**

- **Role:** Infrastructure and deployment coordinator
- **Responsibilities:**
  - Create deployment strategy
  - Manage CI/CD pipelines
  - Monitor production issues
  - Coordinate releases
- **Scaling:** 1 replica

### 7. **Backend Developer Agent**

- **Role:** Server-side code implementation
- **Responsibilities:**
  - Write backend code (APIs, services, database)
  - Follow TDD (write tests first)
  - Implement business logic
  - Handle data models
- **Scaling:** 2-10 replicas (high load)

### 8. **Frontend Developer Agent**

- **Role:** UI/UX code implementation
- **Responsibilities:**
  - Write frontend code (components, pages)
  - Follow TDD (component tests)
  - Implement responsive design
  - Handle state management
- **Scaling:** 2-10 replicas (high load)

### 9. **BDD Test Writer Agent**

- **Role:** Gherkin scenario implementation
- **Responsibilities:**
  - Convert Gherkin to automated tests
  - Write step definitions
  - Create test data
  - Implement BDD framework
- **Scaling:** 1-5 replicas

### 10. **Unit Test Agent**

- **Role:** TDD test implementation
- **Responsibilities:**
  - Write unit tests
  - Execute tests
  - Report failures
  - Measure code coverage
- **Scaling:** 2-8 replicas (high load)

### 11. **Integration Test Agent**

- **Role:** End-to-end testing
- **Responsibilities:**
  - Write integration tests
  - Test API contracts
  - Test database interactions
  - Performance testing
- **Scaling:** 1-5 replicas

### 12. **DevOps Agent**

- **Role:** Infrastructure automation
- **Responsibilities:**
  - Create Docker files
  - Write K8s manifests
  - Setup CI/CD pipelines
  - Configure monitoring
- **Scaling:** 1-3 replicas

---

## Project Structure

```
ai-software-dev-crew/
├── README.md
├── requirements.txt
├── .env.example
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
├── kubernetes/
│   ├── namespace.yaml
│   ├── configmap.yaml
│   ├── secrets.yaml
│   ├── deployments/
│   │   ├── budget-agent.yaml
│   │   ├── ba-agent.yaml
│   │   ├── engineering-manager.yaml
│   │   ├── dev-manager.yaml
│   │   ├── qa-manager.yaml
│   │   ├── ops-manager.yaml
│   │   ├── backend-dev.yaml
│   │   ├── frontend-dev.yaml
│   │   ├── bdd-test-writer.yaml
│   │   ├── unit-test.yaml
│   │   ├── integration-test.yaml
│   │   └── devops-agent.yaml
│   ├── services/
│   │   ├── cache-service.yaml
│   │   ├── postgres-service.yaml
│   │   └── rabbitmq-service.yaml
│   ├── hpa/
│   │   ├── backend-dev-hpa.yaml
│   │   ├── frontend-dev-hpa.yaml
│   │   └── test-agents-hpa.yaml
│   └── pv/
│       ├── code-workspace-pv.yaml
│       └── code-workspace-pvc.yaml
├── config/
│   ├── prompts/
│   │   ├── budget_agent.yaml
│   │   ├── ba_agent.yaml
│   │   ├── engineering_manager.yaml
│   │   ├── dev_manager.yaml
│   │   ├── qa_manager.yaml
│   │   ├── ops_manager.yaml
│   │   ├── backend_developer.yaml
│   │   ├── frontend_developer.yaml
│   │   ├── bdd_test_writer.yaml
│   │   ├── unit_test_agent.yaml
│   │   ├── integration_test_agent.yaml
│   │   └── devops_agent.yaml
│   ├── tools/
│   │   ├── file_tools.yaml
│   │   ├── git_tools.yaml
│   │   └── kubernetes_tools.yaml
│   └── workflows/
│       ├── feature_development.yaml
│       ├── bug_fix.yaml
│       └── release.yaml
├── src/
│   ├── __init__.py
│   ├── main.py
│   ├── budget/
│   │   ├── __init__.py
│   │   ├── tracker.py
│   │   └── limiter.py
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── base_agent.py
│   │   ├── budget_agent.py
│   │   ├── ba_agent.py
│   │   ├── managers/
│   │   │   ├── engineering_manager.py
│   │   │   ├── dev_manager.py
│   │   │   ├── qa_manager.py
│   │   │   └── ops_manager.py
│   │   └── workers/
│   │       ├── backend_developer.py
│   │       ├── frontend_developer.py
│   │       ├── bdd_test_writer.py
│   │       ├── unit_test_agent.py
│   │       ├── integration_test_agent.py
│   │       └── devops_agent.py
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── file_ops.py
│   │   ├── git_ops.py
│   │   ├── test_runner.py
│   │   ├── code_analyzer.py
│   │   └── k8s_ops.py
│   ├── shared/
│   │   ├── __init__.py
│   │   ├── storage.py
│   │   ├── message_queue.py
│   │   ├── state_manager.py
│   │   └── lock_manager.py
│   └── utils/
│       ├── __init__.py
│       ├── prompt_loader.py
│       ├── config_loader.py
│       └── logger.py
└── tests/
    ├── unit/
    ├── integration/
    └── e2e/
```

---

## CrewAI Execution Model

### How Crews Work in This System

1. **Crew as Task Execution Unit:** Each crew is instantiated per task/feature
2. **Manager-Worker Pattern:** Hierarchical crews use manager agents to delegate
3. **Sequential Workflows:** Some crews execute tasks in order (QA, DevOps)
4. **Parallel Execution:** Multiple crews can run simultaneously (Dev + QA + DevOps)
5. **Shared Context:** Crews share state via Redis and PostgreSQL
6. **Budget Control:** All LLM calls intercepted via callbacks

### Crew Lifecycle

```python
# 1. User submits vision
vision = "Build a TODO API with FastAPI"

# 2. Orchestrator creates and runs BA Crew
ba_crew = BACrew()
requirements = ba_crew.kickoff(inputs={"vision": vision})

# 3. Engineering Manager Crew breaks down work
eng_crew = EngineeringCrew()
task_plan = eng_crew.kickoff(inputs={"requirements": requirements})

# 4. Parallel execution of specialized crews
results = await asyncio.gather(
    DevCrew().kickoff(task_plan.dev_tasks),
    QACrew().kickoff(task_plan.qa_tasks),
    DevOpsCrew().kickoff(task_plan.ops_tasks)
)

# 5. BA Crew reviews and signs off
sign_off = ba_crew.kickoff(inputs={"results": results})
```

---

## Implementation Steps

### Phase 1: Local Development Setup

#### Step 1.1: Initialize Project

```bash
# Create project directory
mkdir ai-software-dev-crew
cd ai-software-dev-crew

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # or `venv\Scripts\activate` on Windows

# Create directory structure
mkdir -p src/{agents/{managers,workers},tools,shared,utils,budget}
mkdir -p config/{prompts,tools,workflows}
mkdir -p kubernetes/{deployments,services,hpa,pv}
mkdir -p tests/{unit,integration,e2e}
mkdir -p docker
```

#### Step 1.2: Install Dependencies

Create `requirements.txt`:

```txt
# Core Framework (Latest as of Dec 2024)
crewai==0.86.0
crewai-tools==0.17.0
langchain==0.3.7
langchain-openai==0.2.8
langchain-anthropic==0.3.0
langchain-google-genai==2.0.5

# Message Queue & State Management
redis==5.2.0  # Dragonfly-compatible Redis client
pika==1.3.2  # RabbitMQ client
psycopg2-binary==2.9.10

# File Operations
gitpython==3.1.43
PyYAML==6.0.2

# Testing
pytest==8.3.3
pytest-bdd==7.3.0
pytest-asyncio==0.24.0

# Kubernetes
kubernetes==31.0.0

# Monitoring
prometheus-client==0.21.0

# Utilities
python-dotenv==1.0.1
pydantic==2.10.3
requests==2.32.3
rich==13.9.4  # For beautiful console output
```

Install:

```bash
pip install -r requirements.txt
```

#### Step 1.3: Create Environment Configuration

Create `.env.example`:

```bash
# LLM API Keys
OPENAI_API_KEY=your_openai_key
ANTHROPIC_API_KEY=your_anthropic_key
GOOGLE_API_KEY=your_google_key

# Budget Settings
BUDGET_MAX_COST_PER_PROJECT=100.00
BUDGET_MAX_COST_PER_HOUR=10.00
BUDGET_ALERT_THRESHOLD=0.8

# Cache (Dragonfly or Redis-compatible)
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_PASSWORD=

# RabbitMQ
RABBITMQ_HOST=localhost
RABBITMQ_PORT=5672
RABBITMQ_USER=guest
RABBITMQ_PASSWORD=guest

# PostgreSQL
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=ai_dev_crew
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres

# Workspace
WORKSPACE_PATH=/workspace
GIT_REPO_URL=

# Logging
LOG_LEVEL=INFO
```

Copy and configure:

```bash
cp .env.example .env
# Edit .env with your actual values
```

---

### Phase 2: Externalized Prompts

#### Step 2.1: Create Prompt Templates

Create `config/prompts/budget_agent.yaml`:

```yaml
role: "Budget Controller"

goal: |
  Monitor and control AI token usage across all agents. Block requests that exceed 
  budget limits. Generate cost reports and alerts.

backstory: |
  You are the financial guardian of the AI development team. Your responsibility 
  is to ensure the project stays within budget by tracking every AI API call, 
  calculating costs in real-time, and preventing overspending.

instructions: |
  1. Before any agent makes an LLM call, check if budget allows it
  2. Track token usage: prompt_tokens + completion_tokens
  3. Calculate cost based on model pricing
  4. Update running total in the cache store
  5. If budget exceeded, return error and block the request
  6. Generate hourly cost reports
  7. Alert managers when reaching 80% of budget
  8. Log all budget decisions

tools:
  - redis_get
  - redis_set
  - cost_calculator
  - alert_sender

max_iter: 5
allow_delegation: false
verbose: true
```

Create `config/prompts/ba_agent.yaml`:

```yaml
role: "Business Analyst"

goal: |
  Analyze user vision, create detailed requirements with acceptance criteria using 
  Gherkin syntax (BDD), and provide final sign-off on completed work.

backstory: |
  You are a senior Business Analyst with 15 years of experience in software development. 
  You excel at translating business needs into clear, testable requirements using 
  Behavior-Driven Development (BDD) practices with Gherkin syntax.

instructions: |
  1. Receive user vision statement
  2. Ask clarifying questions if needed
  3. Create comprehensive requirements document with:
     - User stories
     - Gherkin scenarios (Given-When-Then)
     - Acceptance criteria
     - Non-functional requirements
  4. Review completed work against requirements
  5. Accept or reject with detailed feedback
  6. Final sign-off only when ALL criteria met

gherkin_template: |
  Feature: [Feature Name]
    As a [role]
    I want [feature]
    So that [benefit]
    
  Scenario: [Scenario Name]
    Given [precondition]
    When [action]
    Then [expected result]
    And [additional assertion]

tools:
  - document_creator
  - requirement_validator
  - sign_off_tool

max_iter: 10
allow_delegation: true
verbose: true
```

Create `config/prompts/engineering_manager.yaml`:

```yaml
role: "Engineering Manager"

goal: |
  Coordinate all development activities by breaking down requirements into tasks, 
  assigning to appropriate managers, tracking progress, and reporting to BA.

backstory: |
  You are a seasoned Engineering Manager with expertise in agile methodologies, 
  team coordination, and project delivery. You ensure all teams work in harmony 
  and projects are delivered on time with high quality.

instructions: |
  1. Receive requirements from BA
  2. Break down into tasks:
     - Backend development tasks
     - Frontend development tasks
     - Testing tasks (BDD + TDD)
     - DevOps tasks
  3. Assign tasks to appropriate managers:
     - Dev Manager for coding tasks
     - QA Manager for testing tasks
     - DevOps Manager for infrastructure tasks
  4. Monitor progress via message queue
  5. Resolve conflicts and blockers
  6. Aggregate results and report to BA
  7. Request rework if quality issues found

task_assignment_strategy: |
  - Analyze task complexity
  - Check agent availability
  - Consider dependencies
  - Balance workload
  - Prioritize critical path

tools:
  - task_breaker
  - task_assigner
  - progress_tracker
  - conflict_resolver

max_iter: 20
allow_delegation: true
verbose: true
```

Create `config/prompts/dev_manager.yaml`:

```yaml
role: "Development Manager"

goal: |
  Manage code development by assigning tasks to backend and frontend developers, 
  ensuring TDD practices are followed, reviewing code quality, and coordinating 
  with QA Manager.

backstory: |
  You are an experienced Development Manager who champions Test-Driven Development 
  (TDD) and clean code practices. You ensure developers write tests first, then 
  implementation, following SOLID principles.

instructions: |
  1. Receive coding tasks from Engineering Manager
  2. Determine if backend or frontend work
  3. Assign to appropriate developer agents
  4. Ensure TDD workflow:
     - Developer writes test first (red)
     - Developer writes minimal code to pass (green)
     - Developer refactors (refactor)
  5. Review code quality:
     - Test coverage > 80%
     - No code smells
     - Follows style guide
  6. Report completed tasks to Engineering Manager
  7. Report bugs to QA Manager

tdd_checklist: |
  - [ ] Unit tests written before implementation
  - [ ] All tests pass
  - [ ] Code coverage >= 80%
  - [ ] No failing edge cases
  - [ ] Code is refactored and clean

tools:
  - code_reviewer
  - test_coverage_checker
  - style_checker
  - git_operations

max_iter: 15
allow_delegation: true
verbose: true
```

Create `config/prompts/backend_developer.yaml`:

```yaml
role: "Backend Developer"

goal: |
  Implement server-side code following TDD practices. Write unit tests first, 
  then write minimal code to pass tests, then refactor for quality.

backstory: |
  You are a senior backend developer proficient in Python, Node.js, Java, and 
  database design. You strictly follow TDD: Red-Green-Refactor cycle. You write 
  clean, maintainable, and well-tested code.

instructions: |
  1. Receive task from Dev Manager
  2. RED: Write failing unit test first
     - Define expected behavior
     - Write test that fails
  3. GREEN: Write minimal code to pass test
     - Focus on making test pass
     - Don't over-engineer
  4. REFACTOR: Improve code quality
     - Remove duplication
     - Improve naming
     - Apply design patterns
  5. Run all tests to ensure no regression
  6. Commit code with test to Git
  7. Report completion to Dev Manager

languages_supported:
  - Python (FastAPI, Django, Flask)
  - Node.js (Express, NestJS)
  - Java (Spring Boot)
  - Go

coding_standards:
  - Follow PEP 8 (Python) or equivalent
  - Write docstrings/comments
  - Handle errors gracefully
  - Log important operations
  - Validate inputs

tools:
  - file_writer
  - file_reader
  - test_runner
  - git_commit
  - linter

max_iter: 25
allow_delegation: false
verbose: true
```

**Repeat similar YAML files for:**

- `frontend_developer.yaml`
- `bdd_test_writer.yaml`
- `unit_test_agent.yaml`
- `integration_test_agent.yaml`
- `qa_manager.yaml`
- `ops_manager.yaml`
- `devops_agent.yaml`

---

### Phase 3: Budget Control Implementation

#### Step 3.1: Budget Tracker

Create `src/budget/tracker.py`:

```python
"""
Budget tracking and cost calculation for AI agent operations.
"""
import os
import logging
from typing import Dict, Optional
from datetime import datetime
import redis
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class ModelPricing:
    """Pricing per 1M tokens"""
    input_price: float
    output_price: float

# Pricing as of Dec 2025 (per 1M tokens)
MODEL_PRICING = {
    "gpt-4o": ModelPricing(2.50, 10.00),
    "gpt-4o-mini": ModelPricing(0.15, 0.60),
    "claude-3.5-sonnet": ModelPricing(3.00, 15.00),
    "claude-3.5-haiku": ModelPricing(0.80, 4.00),
    "gemini-1.5-pro": ModelPricing(1.25, 5.00),
    "gemini-1.5-flash": ModelPricing(0.075, 0.30),
}

class BudgetTracker:
    """Tracks AI usage and costs across all agents"""

    def __init__(self):
        # Dragonfly (or any Redis-compatible cache) works with the standard client.
        self.redis_client = redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", 6379)),
            password=os.getenv("REDIS_PASSWORD"),
            decode_responses=True
        )
        self.max_cost_per_project = float(os.getenv("BUDGET_MAX_COST_PER_PROJECT", 100.0))
        self.max_cost_per_hour = float(os.getenv("BUDGET_MAX_COST_PER_HOUR", 10.0))
        self.alert_threshold = float(os.getenv("BUDGET_ALERT_THRESHOLD", 0.8))

    def calculate_cost(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int
    ) -> float:
        """Calculate cost for a model call"""
        if model not in MODEL_PRICING:
            logger.warning(f"Unknown model {model}, using default pricing")
            pricing = ModelPricing(3.0, 15.0)  # Default to GPT-4o equivalent
        else:
            pricing = MODEL_PRICING[model]

        input_cost = (input_tokens / 1_000_000) * pricing.input_price
        output_cost = (output_tokens / 1_000_000) * pricing.output_price
        total_cost = input_cost + output_cost

        logger.debug(
            f"Cost calculation: {model} | "
            f"Input: {input_tokens} tokens (${input_cost:.6f}) | "
            f"Output: {output_tokens} tokens (${output_cost:.6f}) | "
            f"Total: ${total_cost:.6f}"
        )

        return total_cost

    def record_usage(
        self,
        project_id: str,
        agent_name: str,
        model: str,
        input_tokens: int,
        output_tokens: int
    ) -> Dict:
        """Record usage and return cost info"""
        cost = self.calculate_cost(model, input_tokens, output_tokens)

        # Update project total
        project_key = f"budget:project:{project_id}"
        self.redis_client.incrbyfloat(project_key, cost)

        # Update hourly total
        hour_key = f"budget:hour:{datetime.now().strftime('%Y-%m-%d-%H')}"
        self.redis_client.incrbyfloat(hour_key, cost)
        self.redis_client.expire(hour_key, 7200)  # Keep for 2 hours

        # Update agent total
        agent_key = f"budget:agent:{agent_name}"
        self.redis_client.incrbyfloat(agent_key, cost)

        # Get current totals
        project_total = float(self.redis_client.get(project_key) or 0)
        hour_total = float(self.redis_client.get(hour_key) or 0)

        return {
            "cost": cost,
            "project_total": project_total,
            "hour_total": hour_total,
            "project_budget_remaining": self.max_cost_per_project - project_total,
            "hour_budget_remaining": self.max_cost_per_hour - hour_total
        }

    def check_budget(self, project_id: str) -> Dict:
        """Check if budget allows more requests"""
        project_key = f"budget:project:{project_id}"
        hour_key = f"budget:hour:{datetime.now().strftime('%Y-%m-%d-%H')}"

        project_total = float(self.redis_client.get(project_key) or 0)
        hour_total = float(self.redis_client.get(hour_key) or 0)

        project_exceeded = project_total >= self.max_cost_per_project
        hour_exceeded = hour_total >= self.max_cost_per_hour

        project_warning = project_total >= (self.max_cost_per_project * self.alert_threshold)
        hour_warning = hour_total >= (self.max_cost_per_hour * self.alert_threshold)

        return {
            "allowed": not (project_exceeded or hour_exceeded),
            "project_exceeded": project_exceeded,
            "hour_exceeded": hour_exceeded,
            "project_warning": project_warning,
            "hour_warning": hour_warning,
            "project_total": project_total,
            "project_limit": self.max_cost_per_project,
            "hour_total": hour_total,
            "hour_limit": self.max_cost_per_hour,
            "message": self._get_budget_message(
                project_exceeded, hour_exceeded, project_warning, hour_warning
            )
        }

    def _get_budget_message(
        self,
        project_exceeded: bool,
        hour_exceeded: bool,
        project_warning: bool,
        hour_warning: bool
    ) -> str:
        """Generate budget status message"""
        if project_exceeded:
            return "❌ PROJECT BUDGET EXCEEDED - Request blocked"
        if hour_exceeded:
            return "❌ HOURLY BUDGET EXCEEDED - Request blocked"
        if project_warning:
            return "⚠️ WARNING: Project budget at 80%"
        if hour_warning:
            return "⚠️ WARNING: Hourly budget at 80%"
        return "✅ Budget OK"

    def get_report(self, project_id: str) -> Dict:
        """Generate cost report"""
        project_key = f"budget:project:{project_id}"
        project_total = float(self.redis_client.get(project_key) or 0)

        # Get per-agent costs
        agent_keys = self.redis_client.keys("budget:agent:*")
        agent_costs = {
            key.split(":")[-1]: float(self.redis_client.get(key) or 0)
            for key in agent_keys
        }

        return {
            "project_id": project_id,
            "total_cost": project_total,
            "budget_limit": self.max_cost_per_project,
            "budget_used_pct": (project_total / self.max_cost_per_project) * 100,
            "budget_remaining": self.max_cost_per_project - project_total,
            "agent_breakdown": agent_costs,
            "timestamp": datetime.now().isoformat()
        }
```

#### Step 3.2: Budget Agent

Create `src/agents/budget_agent.py`:

```python
"""
Budget Agent - Controls AI usage and blocks agents if budget exceeded
"""
import logging
from crewai import Agent
from ..budget.tracker import BudgetTracker
from ..utils.prompt_loader import load_prompt

logger = logging.getLogger(__name__)

class BudgetAgent:
    """Budget control agent"""

    def __init__(self):
        self.tracker = BudgetTracker()
        self.prompt_config = load_prompt("budget_agent")

    def create_agent(self) -> Agent:
        """Create CrewAI budget agent"""
        return Agent(
            role=self.prompt_config["role"],
            goal=self.prompt_config["goal"],
            backstory=self.prompt_config["backstory"],
            tools=[],  # Budget agent doesn't need LLM calls
            allow_delegation=False,
            verbose=True
        )

    def check_budget_before_call(self, project_id: str, agent_name: str) -> bool:
        """
        Check budget before allowing LLM call.
        Returns True if allowed, False if blocked.
        """
        budget_status = self.tracker.check_budget(project_id)

        if not budget_status["allowed"]:
            logger.error(
                f"🚫 BUDGET BLOCKED: {agent_name} | {budget_status['message']}"
            )
            return False

        if budget_status["project_warning"] or budget_status["hour_warning"]:
            logger.warning(
                f"⚠️ BUDGET WARNING: {agent_name} | {budget_status['message']}"
            )

        return True

    def record_call(
        self,
        project_id: str,
        agent_name: str,
        model: str,
        input_tokens: int,
        output_tokens: int
    ) -> Dict:
        """Record LLM call after completion"""
        return self.tracker.record_usage(
            project_id, agent_name, model, input_tokens, output_tokens
        )

    def get_cost_report(self, project_id: str) -> Dict:
        """Generate cost report"""
        return self.tracker.get_report(project_id)
```

---

### Phase 4: Agent Base Class with Budget Control

Create `src/agents/base_agent.py`:

```python
"""
Base agent class with budget control integration
"""
import logging
from typing import Optional, Dict, Any
from crewai import Agent, LLM
from ..budget.tracker import BudgetTracker
from ..utils.prompt_loader import load_prompt

logger = logging.getLogger(__name__)

class BaseAgentWithBudget:
    """Base class for all agents with budget control"""

    def __init__(
        self,
        agent_name: str,
        prompt_file: str,
        project_id: str,
        llm_model: str = "gpt-4o-mini",
        tools: list = None
    ):
        self.agent_name = agent_name
        self.project_id = project_id
        self.llm_model = llm_model
        self.tools = tools or []
        self.tracker = BudgetTracker()

        # Load externalized prompt
        self.prompt_config = load_prompt(prompt_file)

        # Create agent
        self.agent = self._create_agent()

    def _create_agent(self) -> Agent:
        """Create CrewAI agent with config"""

        # Create LLM with callback for budget tracking
        llm = LLM(
            model=self.llm_model,
            callbacks=[self._budget_callback]
        )

        return Agent(
            role=self.prompt_config["role"],
            goal=self.prompt_config["goal"],
            backstory=self.prompt_config["backstory"],
            llm=llm,
            tools=self.tools,
            max_iter=self.prompt_config.get("max_iter", 10),
            allow_delegation=self.prompt_config.get("allow_delegation", False),
            verbose=self.prompt_config.get("verbose", True)
        )

    def _budget_callback(self, response: Any):
        """Callback to track token usage"""
        if hasattr(response, 'usage'):
            usage = response.usage
            self.tracker.record_usage(
                project_id=self.project_id,
                agent_name=self.agent_name,
                model=self.llm_model,
                input_tokens=usage.prompt_tokens,
                output_tokens=usage.completion_tokens
            )

    def check_budget(self) -> bool:
        """Check if budget allows execution"""
        budget_status = self.tracker.check_budget(self.project_id)

        if not budget_status["allowed"]:
            logger.error(
                f"🚫 {self.agent_name} blocked due to budget: {budget_status['message']}"
            )
            raise BudgetExceededException(budget_status["message"])

        return True

    def execute(self, task: str) -> str:
        """Execute agent task with budget check"""
        # Check budget before execution
        self.check_budget()

        # Execute task
        result = self.agent.execute_task(task)

        return result

class BudgetExceededException(Exception):
    """Raised when budget is exceeded"""
    pass
```

---

### Phase 5: Kubernetes Deployment

#### Step 5.1: Docker Image

Create `docker/Dockerfile`:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ ./src/
COPY config/ ./config/

# Create workspace directory
RUN mkdir -p /workspace

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Entry point dispatcher
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
```

Create `docker/entrypoint.sh`:

```bash
#!/bin/bash
set -e

AGENT_NAME=${AGENT_NAME:-"engineering_manager"}

echo "Starting agent: $AGENT_NAME"

python -m src.agents.${AGENT_NAME}
```

Build:

```bash
docker build -t ai-dev-crew:latest -f docker/Dockerfile .
```

#### Step 5.2: Kubernetes Namespace

Create `kubernetes/namespace.yaml`:

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: ai-dev-crew
  labels:
    name: ai-dev-crew
```

#### Step 5.3: ConfigMap for Prompts

Create `kubernetes/configmap.yaml`:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: agent-prompts
  namespace: ai-dev-crew
data:
  budget_agent.yaml: |
    # Content from config/prompts/budget_agent.yaml
  ba_agent.yaml: |
    # Content from config/prompts/ba_agent.yaml
  # ... all other prompt files
```

#### Step 5.4: Secrets

Create `kubernetes/secrets.yaml`:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: ai-api-keys
  namespace: ai-dev-crew
type: Opaque
stringData:
  openai-api-key: "your_openai_key"
  anthropic-api-key: "your_anthropic_key"
  google-api-key: "your_google_key"
  cache-password: "your_cache_password"
  rabbitmq-password: "your_rabbitmq_password"
  postgres-password: "your_postgres_password"
```

#### Step 5.5: Persistent Volume

Create `kubernetes/pv/code-workspace-pv.yaml`:

```yaml
apiVersion: v1
kind: PersistentVolume
metadata:
  name: code-workspace-pv
spec:
  capacity:
    storage: 100Gi
  accessModes:
    - ReadWriteMany
  storageClassName: nfs # or your storage class
  nfs:
    server: your-nfs-server.example.com
    path: "/workspace"
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: code-workspace-pvc
  namespace: ai-dev-crew
spec:
  accessModes:
    - ReadWriteMany
  resources:
    requests:
      storage: 100Gi
  storageClassName: nfs
```

#### Step 5.6: Backend Developer Deployment (Scalable)

Create `kubernetes/deployments/backend-dev.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: backend-developer
  namespace: ai-dev-crew
  labels:
    app: backend-developer
    role: worker
spec:
  replicas: 2 # Start with 2, HPA will scale
  selector:
    matchLabels:
      app: backend-developer
  template:
    metadata:
      labels:
        app: backend-developer
        role: worker
    spec:
      containers:
        - name: backend-developer
          image: ai-dev-crew:latest
          env:
            - name: AGENT_NAME
              value: "backend_developer"
            - name: OPENAI_API_KEY
              valueFrom:
                secretKeyRef:
                  name: ai-api-keys
                  key: openai-api-key
            - name: REDIS_HOST
              value: "cache-service"
            - name: REDIS_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: ai-api-keys
                  key: cache-password
            - name: RABBITMQ_HOST
              value: "rabbitmq-service"
            - name: RABBITMQ_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: ai-api-keys
                  key: rabbitmq-password
            - name: POSTGRES_HOST
              value: "postgres-service"
            - name: POSTGRES_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: ai-api-keys
                  key: postgres-password
            - name: WORKSPACE_PATH
              value: "/workspace"
          volumeMounts:
            - name: workspace
              mountPath: /workspace
            - name: prompts
              mountPath: /app/config/prompts
          resources:
            requests:
              memory: "1Gi"
              cpu: "500m"
            limits:
              memory: "2Gi"
              cpu: "1000m"
      volumes:
        - name: workspace
          persistentVolumeClaim:
            claimName: code-workspace-pvc
        - name: prompts
          configMap:
            name: agent-prompts
```

#### Step 5.7: HPA for Backend Developer

Create `kubernetes/hpa/backend-dev-hpa.yaml`:

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: backend-developer-hpa
  namespace: ai-dev-crew
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: backend-developer
  minReplicas: 2
  maxReplicas: 10
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
    - type: Resource
      resource:
        name: memory
        target:
          type: Utilization
          averageUtilization: 80
  behavior:
    scaleDown:
      stabilizationWindowSeconds: 300
      policies:
        - type: Percent
          value: 50
          periodSeconds: 60
    scaleUp:
      stabilizationWindowSeconds: 0
      policies:
        - type: Percent
          value: 100
          periodSeconds: 30
```

**Repeat similar deployments and HPAs for all agents.**

#### Step 5.8: Budget Agent (Singleton)

Create `kubernetes/deployments/budget-agent.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: budget-agent
  namespace: ai-dev-crew
  labels:
    app: budget-agent
    role: guardian
spec:
  replicas: 1 # Singleton - no scaling
  selector:
    matchLabels:
      app: budget-agent
  template:
    metadata:
      labels:
        app: budget-agent
        role: guardian
    spec:
      containers:
        - name: budget-agent
          image: ai-dev-crew:latest
          env:
            - name: AGENT_NAME
              value: "budget_agent"
            - name: REDIS_HOST
              value: "cache-service"
            - name: REDIS_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: ai-api-keys
                  key: cache-password
            - name: BUDGET_MAX_COST_PER_PROJECT
              value: "100.00"
            - name: BUDGET_MAX_COST_PER_HOUR
              value: "10.00"
          resources:
            requests:
              memory: "256Mi"
              cpu: "100m"
            limits:
              memory: "512Mi"
              cpu: "200m"
```

#### Step 5.9: Deploy to Kubernetes

```bash
# Apply namespace
kubectl apply -f kubernetes/namespace.yaml

# Apply secrets (update with real values first!)
kubectl apply -f kubernetes/secrets.yaml

# Apply ConfigMap
kubectl apply -f kubernetes/configmap.yaml

# Apply PV and PVC
kubectl apply -f kubernetes/pv/

# Deploy infrastructure (cache store, RabbitMQ, PostgreSQL)
kubectl apply -f kubernetes/services/

# Deploy agents
kubectl apply -f kubernetes/deployments/

# Apply HPAs
kubectl apply -f kubernetes/hpa/

# Verify
kubectl get pods -n ai-dev-crew
kubectl get hpa -n ai-dev-crew
```

---

### Phase 6: Customization Guide

#### How to Customize Agent Prompts

1. **Edit YAML file:**

   ```bash
   vim config/prompts/backend_developer.yaml
   ```

2. **Update ConfigMap:**

   ```bash
kubectl create configmap agent-prompts \
  --from-file=config/prompts/ \
  --namespace ai-dev-crew \
  --dry-run=client -o yaml \
  | kubectl apply -n ai-dev-crew -f -
   ```

3. **Restart affected agents:**
   ```bash
   kubectl rollout restart deployment/backend-developer -n ai-dev-crew
   ```

#### How to Adjust Budget Limits

1. **Update environment variables in deployment:**

   ```yaml
   env:
     - name: BUDGET_MAX_COST_PER_PROJECT
       value: "200.00" # Increased from 100
   ```

2. **Or use ConfigMap/Secret for dynamic updates**

#### How to Add New Agent

1. Create prompt file: `config/prompts/new_agent.yaml`
2. Implement agent class: `src/agents/workers/new_agent.py`
3. Create deployment: `kubernetes/deployments/new-agent.yaml`
4. Apply: `kubectl apply -f kubernetes/deployments/new-agent.yaml`

---

## Workflow Example

### Feature Development Flow

```
1. User provides vision
   ↓
2. BA Agent creates requirements (Gherkin scenarios)
   ↓
3. Engineering Manager breaks down into tasks
   ↓
4. Dev Manager assigns to Backend/Frontend Developers
   ↓
5. Developers follow TDD:
   - Write test (RED)
   - Write code (GREEN)
   - Refactor
   ↓
6. BDD Test Writer converts Gherkin to automated tests
   ↓
7. Unit Test Agent runs all tests
   ↓
8. Integration Test Agent runs E2E tests
   ↓
9. QA Manager reviews test results
   ↓
10. If tests fail → report to Dev Manager → fix
    If tests pass → continue
   ↓
11. DevOps Agent creates Docker + K8s manifests
   ↓
12. Engineering Manager aggregates all results
   ↓
13. BA reviews and signs off
   ↓
14. DONE ✅
```

**Budget Agent monitors every step and blocks if limit exceeded.**

---

## Monitoring & Observability

### Cost Dashboard

Query the cache store (using `redis-cli` against Dragonfly) for metrics:

```bash
# Project total cost
redis-cli GET "budget:project:PROJECT_ID"

# Agent breakdown
redis-cli KEYS "budget:agent:*"

# Current hour usage
redis-cli GET "budget:hour:2025-12-01-14"
```

### Prometheus Metrics

Expose metrics endpoint:

```python
from prometheus_client import Counter, Gauge, Histogram

llm_calls_total = Counter('llm_calls_total', 'Total LLM calls', ['agent', 'model'])
llm_cost_total = Counter('llm_cost_total', 'Total LLM cost', ['agent'])
budget_remaining = Gauge('budget_remaining', 'Budget remaining', ['project'])
```

---

## Testing

### Unit Tests

```bash
pytest tests/unit/ -v
```

### Integration Tests

```bash
pytest tests/integration/ -v
```

### End-to-End Test

```bash
python -m src.main --vision "Build a simple TODO API with FastAPI"
```

---

## Summary

This architecture provides:

✅ **Scalable agents** - Independent scaling per role
✅ **Budget control** - Hard limits with blocking
✅ **Externalized prompts** - Easy customization
✅ **Manager hierarchy** - Proper coordination
✅ **BDD + TDD** - Quality practices enforced
✅ **Kubernetes native** - Production-ready deployment
✅ **Observable** - Full monitoring and cost tracking

**Next Steps:**

1. Implement remaining agent classes
2. Add file locking via the cache store
3. Implement message queue handlers
4. Create monitoring dashboards
5. Add CI/CD pipelines
6. Deploy to production K8s cluster

Good luck building your AI software development crew! 🚀
