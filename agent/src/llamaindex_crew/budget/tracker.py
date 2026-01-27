"""
Budget tracking and cost calculation for AI agent operations.
Enhanced with robust token estimation, fallback mechanisms, and circuit breaker.
Preserved from original implementation - will be adapted for LlamaIndex callbacks later.
"""
import os
import logging
import time
import re
from typing import Dict, Optional, Any, List, Callable
from datetime import datetime
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

@dataclass
class ModelPricing:
    """Pricing per 1M tokens"""
    input_price: float
    output_price: float

# Pricing as of Dec 2024 (per 1M tokens)
MODEL_PRICING = {
    "gpt-4o": ModelPricing(2.50, 10.00),
    "gpt-4o-mini": ModelPricing(0.15, 0.60),
    "gpt-4": ModelPricing(30.00, 60.00),
    "gpt-4-turbo": ModelPricing(10.00, 30.00),
    "claude-3.5-sonnet": ModelPricing(3.00, 15.00),
    "claude-3.5-haiku": ModelPricing(0.80, 4.00),
    "claude-3-opus": ModelPricing(15.00, 75.00),
    "gemini-1.5-pro": ModelPricing(1.25, 5.00),
    "gemini-1.5-flash": ModelPricing(0.075, 0.30),
}

class BudgetTracker:
    """Tracks AI usage and costs across all agents"""

    def __init__(self):
        # For now, use in-memory tracking. In production, use Redis/Dragonfly
        self._costs: Dict[str, float] = {}
        self._hourly_costs: Dict[str, float] = {}
        self._agent_costs: Dict[str, float] = {}
        
        self.max_cost_per_project = float(os.getenv("BUDGET_MAX_COST_PER_PROJECT", 100.0))
        self.max_cost_per_hour = float(os.getenv("BUDGET_MAX_COST_PER_HOUR", 10.0))
        self.alert_threshold = float(os.getenv("BUDGET_ALERT_THRESHOLD", 0.8))
        self.project_id = os.getenv("PROJECT_ID", "default-project")

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
        self._costs[project_key] = self._costs.get(project_key, 0.0) + cost

        # Update hourly total
        hour_key = f"budget:hour:{datetime.now().strftime('%Y-%m-%d-%H')}"
        self._hourly_costs[hour_key] = self._hourly_costs.get(hour_key, 0.0) + cost

        # Update agent total
        agent_key = f"budget:agent:{agent_name}"
        self._agent_costs[agent_key] = self._agent_costs.get(agent_key, 0.0) + cost

        # Get current totals
        project_total = self._costs.get(project_key, 0.0)
        hour_total = self._hourly_costs.get(hour_key, 0.0)

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

        project_total = self._costs.get(project_key, 0.0)
        hour_total = self._hourly_costs.get(hour_key, 0.0)

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
        project_total = self._costs.get(project_key, 0.0)

        # Get per-agent costs
        agent_costs = {}
        for key, cost in self._agent_costs.items():
            if key.startswith(f"budget:agent:"):
                agent_name = key.split(":")[-1]
                agent_costs[agent_name] = cost

        return {
            "project_id": project_id,
            "total_cost": project_total,
            "budget_limit": self.max_cost_per_project,
            "budget_used_pct": (project_total / self.max_cost_per_project) * 100 if self.max_cost_per_project > 0 else 0,
            "budget_remaining": self.max_cost_per_project - project_total,
            "agent_breakdown": agent_costs,
            "timestamp": datetime.now().isoformat()
        }

    def check_budget_safe(self, project_id: str) -> Dict:
        """Check budget and return safe status (doesn't raise)"""
        return self.check_budget(project_id)


class EnhancedBudgetTracker(BudgetTracker):
    """Enhanced budget tracker with additional features"""
    
    def __init__(self):
        super().__init__()
        self._circuit_breaker_enabled = True
        self._circuit_breaker_threshold = 0.95  # 95% of budget
    
    def check_budget_safe(self, project_id: str) -> Dict:
        """Check budget with circuit breaker"""
        status = super().check_budget_safe(project_id)
        
        # Circuit breaker: if budget is very high, be more conservative
        project_total = status.get("project_total", 0.0)
        if project_total >= (self.max_cost_per_project * self._circuit_breaker_threshold):
            status["allowed"] = False
            status["message"] = "🚫 Circuit breaker: Budget threshold reached"
        
        return status
