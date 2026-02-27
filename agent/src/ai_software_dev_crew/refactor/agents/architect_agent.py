"""
Refactor Architect Agent
Analyzes legacy code and designs the transformation plan.

Flow:
  Phase 1  analyze()  → current_architecture.md   (what exists today)
  Phase 2  design()   → target_architecture.md     (desired future state)
  Phase 3  plan()     → refactor_plan.json + refactor_strategy.md
                         (file-level tasks to get from current → target)
"""
from agent.src.llamaindex_crew.agents.base_agent import BaseLlamaIndexAgent
from agent.src.llamaindex_crew.tools.file_tools import create_workspace_file_tools


class RefactorArchitectAgent(BaseLlamaIndexAgent):
    """
    Specialized Architect that:
    1. Analyzes existing code structure  → current_architecture.md
    2. Designs the target architecture   → target_architecture.md
    3. Creates the task-level plan       → refactor_plan.json
    """

    def __init__(self, workspace_path: str, job_id: str):
        self.workspace_path = workspace_path
        # Standard file tools (read, write, list) scoped to workspace for thread safety
        tools = create_workspace_file_tools(workspace_path)
        
        super().__init__(
            role="Refactor Architect",
            goal="Analyze legacy code, design the target architecture, and create a migration plan",
            backstory=(
                "You are an expert Software Architect specializing in modernization. "
                "You can read any legacy codebase (Java EE, Python 2, Old React) and "
                "map it to modern patterns (Spring Boot 3, FastAPI, React 18). "
                "You are pragmatic: you prefer incremental refactoring over rewriting from scratch."
            ),
            tools=tools,
            agent_type="planner",
            verbose=True
        )

    def analyze(self, source_path: str) -> str:
        """Phase 1: detailed analysis of the current state."""
        prompt = f"""
        ANALYZE MODE
        
        I need you to analyze the codebase at: {source_path}
        
        Your task:
        1. Explore the file structure using file_lister.
        2. Identify key components (entry points, database models, API controllers).
        3. Identify dependencies (pom.xml, requirements.txt, package.json).
        
        Output a file named 'current_architecture.md' describing what you found.
        """
        return str(self.agent.chat(prompt))

    def design(self, target_stack: str, tech_preferences: str = "") -> str:
        """Phase 2: design the desired future-state architecture.

        Reads ``current_architecture.md`` (produced by :meth:`analyze`) and
        writes ``target_architecture.md`` — a comprehensive blueprint of the
        modernised system **before** any code is changed.
        """
        prompt = f"""
        DESIGN MODE — Target Architecture

        Target Stack: {target_stack}
        Tech Preferences: {tech_preferences}

        You have already produced 'current_architecture.md' describing the
        legacy system.  Now design the **target (future-state) architecture**.

        CRITICAL ARCHITECTURE RULES:
        1. Role: You are a Cloud Native Architect and Domain-Driven Design Expert.
        2. 12-Factor App: Enforce stateless processes, config via environment
           variables, and external backing services.
        3. Domain-Driven Design (DDD): Organize code by Bounded Contexts
           (e.g., billing/, inventory/) instead of technical layers.
        4. Cloud Native: Design for containers (OCI images), health probes,
           graceful shutdown, and horizontal scaling.

        Your task — create a file named 'target_architecture.md' containing:

        1. **High-Level Overview** — one-paragraph summary of the modernised
           system and the stack it runs on.
        2. **Component / Service Layout** — list every major component or
           microservice, its responsibility, and which bounded context it
           belongs to.
        3. **Directory Structure** — the intended project tree (top-level
           folders/packages and what lives in each).
        4. **API & Integration Points** — REST / gRPC / messaging contracts
           between components; external integrations.
        5. **Data Model & Storage** — databases, caches, and how data flows
           between components.
        6. **Cross-Cutting Concerns** — authentication, logging, observability,
           configuration management, error handling.
        7. **Key Technology Choices** — frameworks, libraries, and versions
           for the target stack.
        8. **Mapping from Current → Target** — a table or list showing which
           legacy component maps to which target component, highlighting
           components that are new, removed, or merged.

        This document is the "north star" for the executor that will implement
        the refactor tasks.  Be specific and concrete.
        """
        return str(self.agent.chat(prompt))

    def plan(self, target_stack: str, tech_preferences: str = "") -> str:
        """Phase 3: create the file-level refactor plan.

        Reads both ``current_architecture.md`` and ``target_architecture.md``
        and produces the task list that transforms one into the other.
        """
        prompt = f"""
        PLANNING MODE
        
        Target Stack: {target_stack}
        Tech Preferences: {tech_preferences}
        
        You have already produced:
          • 'current_architecture.md'  — the legacy system as it exists today.
          • 'target_architecture.md'   — the desired future-state architecture.

        Based on BOTH documents, create a detailed migration plan — the set of
        file-level tasks that will transform the current codebase into the
        target architecture.
        
        CRITICAL ARCHITECTURE RULES:
        1. Role: You are a Cloud Native Architect and Domain-Driven Design Expert.
        2. 12-Factor App: Enforce stateless processes, config via environment variables, and external backing services.
        3. Domain-Driven Design (DDD): Organize code by Bounded Contexts (e.g., billing/, inventory/) instead of technical layers.
        
        IMPORTANT: All file paths MUST be relative to the workspace root (e.g. "src/main/App.java", NOT absolute paths).
        
        Your task:
        1. Create a file named 'refactor_plan.json' with this structure:
        {{
            "target_stack": "{target_stack}",
            "tasks": [
                {{
                    "id": "1",
                    "file": "src/main/OldService.java",
                    "action": "modify|delete|create",
                    "instruction": "Replace javax.servlet with Jakarta REST..."
                }}
            ]
        }}
        
        2. Also create a human-readable 'refactor_strategy.md' explaining your approach.

        Ensure every task's instruction references the relevant section of
        'target_architecture.md' so the executor knows the end-state goal.
        """
        return str(self.agent.chat(prompt))
