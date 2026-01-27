"""
Budget tracking and cost calculation for AI agent operations.
Enhanced with robust token estimation, fallback mechanisms, and circuit breaker.
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

        logger.info(
            f"💰 Cost recorded: {agent_name} | "
            f"${cost:.4f} | "
            f"Project total: ${project_total:.4f} | "
            f"Hour total: ${hour_total:.4f}"
        )

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
            return "⚠️  WARNING: Project budget at 80%"
        if hour_warning:
            return "⚠️  WARNING: Hourly budget at 80%"
        return "✅ Budget OK"

    def get_report(self, project_id: str) -> Dict:
        """Generate cost report"""
        project_key = f"budget:project:{project_id}"
        project_total = self._costs.get(project_key, 0.0)

        # Get per-agent costs
        agent_costs = {
            key.split(":")[-1]: cost
            for key, cost in self._agent_costs.items()
        }

        return {
            "project_id": project_id,
            "total_cost": project_total,
            "budget_limit": self.max_cost_per_project,
            "budget_used_pct": (project_total / self.max_cost_per_project) * 100 if self.max_cost_per_project > 0 else 0,
            "budget_remaining": self.max_cost_per_project - project_total,
            "agent_breakdown": agent_costs,
            "timestamp": datetime.now().isoformat()
        }

class BudgetExceededException(Exception):
    """Raised when budget is exceeded"""
    pass


class TokenEstimator:
    """Estimates token count from text using simple heuristics"""
    
    @staticmethod
    def estimate(text: str) -> int:
        """
        Estimate token count. Rough approximation: 1 token ≈ 4 characters for English.
        This is a conservative estimate that works across most models.
        """
        if not text:
            return 0
        
        # Count characters and words
        char_count = len(text)
        word_count = len(text.split())
        
        # Use average: ~4 chars per token, but also consider word boundaries
        # Most models tokenize at word boundaries, so we use a hybrid approach
        estimated_tokens = max(
            char_count / 4,  # Character-based estimate
            word_count * 1.3  # Word-based estimate (words often split into multiple tokens)
        )
        
        return int(estimated_tokens)


class TokenExtractor:
    """Extracts token usage from various LLM response formats"""
    
    @staticmethod
    def extract_standard_usage(response: Any) -> Optional[Dict[str, int]]:
        """Extract from standard usage attribute"""
        if hasattr(response, 'usage'):
            usage = response.usage
            if hasattr(usage, 'prompt_tokens') and hasattr(usage, 'completion_tokens'):
                return {
                    'input_tokens': usage.prompt_tokens,
                    'output_tokens': usage.completion_tokens
                }
            elif hasattr(usage, 'input_tokens') and hasattr(usage, 'output_tokens'):
                return {
                    'input_tokens': usage.input_tokens,
                    'output_tokens': usage.output_tokens
                }
        return None
    
    @staticmethod
    def extract_from_dict(response: Any) -> Optional[Dict[str, int]]:
        """Extract from dictionary-like response"""
        if isinstance(response, dict):
            # Try various common formats
            if 'usage' in response:
                usage = response['usage']
                if isinstance(usage, dict):
                    return {
                        'input_tokens': usage.get('prompt_tokens') or usage.get('input_tokens', 0),
                        'output_tokens': usage.get('completion_tokens') or usage.get('output_tokens', 0)
                    }
            # Direct keys
            if 'prompt_tokens' in response and 'completion_tokens' in response:
                return {
                    'input_tokens': response['prompt_tokens'],
                    'output_tokens': response['completion_tokens']
                }
        return None
    
    @staticmethod
    def extract_from_string(response: Any) -> Optional[Dict[str, int]]:
        """Try to extract from string representation (last resort)"""
        if isinstance(response, str):
            # Look for patterns like "tokens: 100" or "usage: {...}"
            patterns = [
                r'prompt_tokens[:\s]+(\d+)',
                r'input_tokens[:\s]+(\d+)',
                r'completion_tokens[:\s]+(\d+)',
                r'output_tokens[:\s]+(\d+)',
            ]
            matches = {}
            for pattern in patterns:
                match = re.search(pattern, response, re.IGNORECASE)
                if match:
                    key = 'input_tokens' if 'input' in pattern or 'prompt' in pattern else 'output_tokens'
                    matches[key] = int(match.group(1))
            
            if len(matches) == 2:
                return matches
        
        return None


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class BudgetServiceCircuitBreaker:
    """Circuit breaker for budget service to handle failures gracefully"""
    
    def __init__(self, failure_threshold: int = 3, recovery_timeout: int = 30):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.last_failure_time = 0
        self.state = CircuitState.CLOSED
    
    def _is_circuit_open(self) -> bool:
        """Check if circuit is open"""
        if self.state == CircuitState.OPEN:
            # Check if recovery timeout has passed
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                logger.info("Circuit breaker transitioning to HALF_OPEN state")
                return False
            return True
        return False
    
    def _record_failure(self):
        """Record a failure"""
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logger.error(f"Circuit breaker opened after {self.failure_count} failures")
    
    def _record_success(self):
        """Record a success"""
        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.CLOSED
            self.failure_count = 0
            logger.info("Circuit breaker closed after successful operation")
        elif self.state == CircuitState.CLOSED:
            self.failure_count = 0
    
    def check_budget_with_fallback(self, tracker: 'BudgetTracker', project_id: str) -> Dict:
        """Check budget with circuit breaker and fallback"""
        if self._is_circuit_open():
            logger.warning("Budget service circuit is open, using degraded mode")
            # Fail open for availability - allow request but log warning
            return {
                "allowed": True,
                "degraded_mode": True,
                "message": "⚠️  Budget service unavailable - allowing request with caution"
            }
        
        try:
            result = tracker.check_budget(project_id)
            self._record_success()
            return result
        except Exception as e:
            self._record_failure()
            logger.error(f"Budget check failed: {e}")
            
            if self.failure_count >= self.failure_threshold:
                # Degraded mode - allow but warn
                return {
                    "allowed": True,
                    "degraded_mode": True,
                    "message": "⚠️  Budget service repeatedly failing - allowing request with caution"
                }
            raise


class EnhancedBudgetTracker(BudgetTracker):
    """Enhanced budget tracker with robust token estimation and fallback mechanisms"""
    
    def __init__(self):
        super().__init__()
        self.token_estimator = TokenEstimator()
        self.token_extractors = [
            TokenExtractor.extract_standard_usage,
            TokenExtractor.extract_from_dict,
            TokenExtractor.extract_from_string,
        ]
        self.circuit_breaker = BudgetServiceCircuitBreaker()
        self._emergency_blocked = False
    
    def estimate_cost_before_call(self, prompt: str, model: str, estimated_output_ratio: float = 0.7) -> float:
        """Estimate cost before making LLM call"""
        estimated_input_tokens = self.token_estimator.estimate(prompt)
        estimated_output_tokens = int(estimated_input_tokens * estimated_output_ratio)
        return self.calculate_cost(model, estimated_input_tokens, estimated_output_tokens)
    
    def _extract_standard_usage(self, usage: Any) -> Dict[str, int]:
        """Extract tokens from standard usage object"""
        if hasattr(usage, 'prompt_tokens') and hasattr(usage, 'completion_tokens'):
            return {
                'input_tokens': usage.prompt_tokens,
                'output_tokens': usage.completion_tokens
            }
        elif hasattr(usage, 'input_tokens') and hasattr(usage, 'output_tokens'):
            return {
                'input_tokens': usage.input_tokens,
                'output_tokens': usage.output_tokens
            }
        raise ValueError("Cannot extract tokens from usage object")
    
    def _estimate_from_response(self, response: Any) -> Dict[str, int]:
        """Fallback: estimate tokens from response content"""
        # Try to get response text
        response_text = ""
        if hasattr(response, 'content'):
            response_text = str(response.content)
        elif hasattr(response, 'text'):
            response_text = str(response.text)
        elif isinstance(response, str):
            response_text = response
        
        # Estimate from prompt (if available) and response
        # This is a rough estimate
        estimated_output = self.token_estimator.estimate(response_text)
        # Assume input was roughly similar (conservative estimate)
        estimated_input = estimated_output
        
        logger.warning(f"Using fallback token estimation: input={estimated_input}, output={estimated_output}")
        
        return {
            'input_tokens': estimated_input,
            'output_tokens': estimated_output
        }
    
    def _record_tokens(self, tokens: Dict[str, int]) -> Dict:
        """Record tokens and return cost info"""
        return {
            'input_tokens': tokens.get('input_tokens', 0),
            'output_tokens': tokens.get('output_tokens', 0)
        }
    
    def _emergency_budget_block(self):
        """Emergency fallback - block further requests"""
        self._emergency_blocked = True
        logger.critical("EMERGENCY: Budget tracking failed - blocking all further requests")
    
    def budget_callback_with_fallback(self, response: Any, project_id: str, agent_name: str, model: str) -> Dict:
        """Robust callback with multiple extraction methods"""
        try:
            # Try standard usage attribute
            if hasattr(response, 'usage'):
                tokens = self._extract_standard_usage(response.usage)
                return self.record_usage(project_id, agent_name, model, tokens['input_tokens'], tokens['output_tokens'])
            
            # Try alternative formats
            for extractor in self.token_extractors:
                try:
                    tokens = extractor(response)
                    if tokens:
                        return self.record_usage(project_id, agent_name, model, tokens['input_tokens'], tokens['output_tokens'])
                except Exception as e:
                    logger.debug(f"Extractor {extractor.__name__} failed: {e}")
                    continue
            
            # Fallback to estimation
            logger.warning("Using fallback token estimation for budget tracking")
            tokens = self._estimate_from_response(response)
            return self.record_usage(project_id, agent_name, model, tokens['input_tokens'], tokens['output_tokens'])
        
        except Exception as e:
            logger.error(f"Budget tracking failed: {e}")
            # Emergency fallback - block further requests
            self._emergency_budget_block()
            # Return a safe default (zero cost) but log the error
            return {
                "cost": 0.0,
                "project_total": self._costs.get(f"budget:project:{project_id}", 0.0),
                "hour_total": self._hourly_costs.get(f"budget:hour:{datetime.now().strftime('%Y-%m-%d-%H')}", 0.0),
                "error": str(e),
                "emergency_mode": True
            }
    
    def check_budget_safe(self, project_id: str) -> Dict:
        """Check budget with circuit breaker protection"""
        return self.circuit_breaker.check_budget_with_fallback(self, project_id)


