import json
import logging
from typing import List, Any
from ..agents.base_agent import BaseLlamaIndexAgent

logger = logging.getLogger(__name__)

def decide_workflow_phases(vision: str, budget_tracker=None) -> List[Any]:
    """
    Analyzes the vision and dynamically decides the workflow pipeline.
    """
    logger.info("🧠 Smart Workflow Router: Analyzing vision to construct dynamic pipeline...")
    
    prompt = f"""You are the Smart Workflow Router. Your job is to determine the optimal execution pipeline based on the project vision.

Project Vision:
{vision}

Available execution phases:
- meta (extracts project DNA and backstories)
- stack_contract (designs the architecture and stack)
- product_owner (writes detailed user stories)
- designer (creates UX/UI design specifications)
- tech_architect (designs the software architecture)
- qa (writes and fixes automated tests)
- development (writes backend/core logic)
- frontend (writes frontend UI code)
- devops (writes Containerfiles and CI/CD pipelines)
- refinement (edits/refactors existing codebase based on instructions)
- seed_minimal_artifacts (auto-generates basic planning artifacts for fast pipelines)

Rules:
1. You must return ONLY a valid JSON list of phases. No markdown formatting, no explanations.
2. AGGRESSIVE FAST LANE: For simple/fast scripts, or if the vision explicitly mentions "no tests", "simple", or "minimal", YOU MUST skip ALL heavy planning phases (product_owner, designer, tech_architect, qa). You must use ONLY 'meta', 'stack_contract', 'seed_minimal_artifacts', and 'development' (or parallel execution).
3. For large-scale refactoring or complex apps, you should include 'stack_contract' and full planning phases.
4. Parallel execution can be expressed as a dictionary: {{"parallel": ["development", "frontend"]}}.
5. CRITICAL TDD CONSTRAINT: If testing or QA is actually required for this vision, you must adhere to Test-Driven Development (TDD) principles and place the `qa` phase BEFORE `development` or `refinement`. However, if the vision implies a trivial script, DO NOT include `qa` or `product_owner` to avoid unnecessary BDD overhead.

Example output for a simple vision:
[
  "meta",
  "stack_contract",
  "seed_minimal_artifacts",
  {{"parallel": ["development", "frontend"]}}
]
"""

    agent = BaseLlamaIndexAgent(
        role='Smart Router',
        goal='Determine the optimal execution pipeline',
        backstory='You are an AI architect responsible for workflow orchestration.',
        tools=[],
        agent_type="manager",
        budget_tracker=budget_tracker,
        verbose=True
    )
    
    try:
        response_str = agent.execute(prompt)
        # clean markdown block if present
        if response_str.startswith("```json"):
            response_str = response_str[7:]
        if response_str.endswith("```"):
            response_str = response_str[:-3]
            
        phases = json.loads(response_str.strip())
        logger.info("🧠 Smart Workflow Router generated pipeline: %s", phases)
        if not isinstance(phases, list):
            raise ValueError("Expected a JSON list of phases.")
        return phases
    except Exception as e:
        logger.error(f"Smart Workflow Router failed: {e}. Falling back to default 'fast' pipeline.")
        from .workflow_resolver import FALLBACK_PIPELINES
        return list(FALLBACK_PIPELINES["fast"])
