"""
Base agent class for LlamaIndex agents
Provides common functionality for all agents including budget tracking and tool integration
"""
import logging
import nest_asyncio
from typing import List, Optional, Callable, Any, Dict
from llama_index.core.agent import ReActAgent
from llama_index.core.llms import LLM
from llama_index.core.tools import FunctionTool

# Apply nest_asyncio to allow nested event loops
nest_asyncio.apply()

# Try to import OpenAIAgent, fallback to ReActAgent if not available
try:
    from llama_index.agent.openai import OpenAIAgent
except ImportError:
    try:
        from llama_index.agents.openai import OpenAIAgent
    except ImportError:
        # Use ReActAgent as fallback
        OpenAIAgent = None
from ..utils.llm_config import get_llm_for_agent
from ..budget.tracker import BudgetTracker, EnhancedBudgetTracker

logger = logging.getLogger(__name__)


class BaseLlamaIndexAgent:
    """Base agent class with budget tracking and tool support"""
    
    def __init__(
        self,
        role: str,
        goal: str,
        backstory: str,
        tools: List[FunctionTool],
        agent_type: str = "worker",
        llm: Optional[LLM] = None,
        budget_tracker: Optional[BudgetTracker] = None,
        verbose: bool = True
    ):
        """
        Initialize base agent
        
        Args:
            role: Agent role (e.g., "Product Owner")
            goal: Agent goal
            backstory: Agent backstory/persona
            tools: List of FunctionTool instances
            agent_type: Type of agent (manager, worker, reviewer)
            llm: Optional LLM instance (will be created if not provided)
            budget_tracker: Optional budget tracker instance
            verbose: Whether to enable verbose logging
        """
        self.role = role
        self.goal = goal
        self.backstory = backstory
        self.tools = tools
        self.agent_type = agent_type
        self.verbose = verbose
        
        # Initialize budget tracker
        self.budget_tracker = budget_tracker or EnhancedBudgetTracker()
        
        # Create LLM with budget callback if not provided
        if llm is None:
            self.llm = get_llm_for_agent(agent_type, budget_callback=self._budget_callback)
        else:
            self.llm = llm
        
        # Create agent instance
        self.agent = self._create_agent()
    
    def _create_agent(self):
        """Create LlamaIndex agent with tools and configuration"""
        # Use ReActAgent (available in all versions)
        # ReActAgent is instantiated directly with tools and llm
        
        # We use a simplified context for the agent to prevent it from hallucinating tools
        # or getting confused by complex thought patterns.
        agent_context = f"""{self._build_system_prompt()}

You are an AI assistant that uses tools to accomplish tasks.
Follow the thought-action-input format:
Thought: I need to use a tool to...
Action: tool_name
Action Input: {{'arg': 'value'}}
Observation: tool output...
... (repeat if needed)
Thought: I have finished the task.
Final Answer: [your response]

IMPORTANT: Only use the tools provided to you. If no tools are needed, provide a Final Answer directly.
"""

        agent = ReActAgent.from_tools(
            tools=self.tools,
            llm=self.llm,
            verbose=self.verbose,
            max_iterations=50, # Increased to allow for more complex multi-file tasks
            system_prompt=agent_context
        )
        return agent
    
    def _build_system_prompt(self) -> str:
        """Build system prompt from role, goal, and backstory"""
        prompt = f"""You are a {self.role}.

Your goal: {self.goal}

Your background and expertise:
{self.backstory}

You have access to the following tools:
{', '.join([tool.metadata.name for tool in self.tools]) if self.tools else 'No tools available.'}

IMPORTANT INSTRUCTIONS:
1. If you can accomplish the task without tools, provide your Final Answer immediately.
2. Do NOT hallucinate or invent tool names. Only use the tools listed above.
3. If no tools are needed for a textual response, do NOT try to use any. Just give the Final Answer.
4. Always follow the required format: Thought -> Action -> Action Input -> Observation OR Final Answer."""
        return prompt
    
    def _budget_callback(self, response: Any) -> None:
        """Callback to track token usage for budget tracking"""
        try:
            # Extract token usage from response
            # LlamaIndex response format may vary
            input_tokens = 0
            output_tokens = 0
            model = 'unknown'
            
            # Try different response formats
            if hasattr(response, 'raw') and hasattr(response.raw, 'usage'):
                usage = response.raw.usage
                input_tokens = getattr(usage, 'prompt_tokens', 0) or getattr(usage, 'total_tokens', 0)
                output_tokens = getattr(usage, 'completion_tokens', 0)
                model = getattr(response.raw, 'model', 'unknown')
            elif hasattr(response, 'usage'):
                usage = response.usage
                input_tokens = getattr(usage, 'prompt_tokens', 0) or getattr(usage, 'total_tokens', 0)
                output_tokens = getattr(usage, 'completion_tokens', 0)
                model = getattr(response, 'model', 'unknown')
            elif hasattr(response, 'response_metadata'):
                # Try response_metadata format
                metadata = response.response_metadata
                if 'token_usage' in metadata:
                    usage = metadata['token_usage']
                    input_tokens = usage.get('prompt_tokens', 0) or usage.get('total_tokens', 0)
                    output_tokens = usage.get('completion_tokens', 0)
                model = metadata.get('model', 'unknown')
            
            # Only record if we have valid token counts
            if input_tokens > 0 or output_tokens > 0:
                self.budget_tracker.record_usage(
                    project_id=self.budget_tracker.project_id,
                    agent_name=self.role,
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens
                )
        except Exception as e:
            logger.debug(f"Could not track budget from response: {e}")
    
    def chat(self, message: str, **kwargs) -> str:
        """
        Chat with the agent
        
        Args:
            message: User message
            **kwargs: Additional arguments for agent run
        
        Returns:
            Agent response
        """
        import asyncio
        
        # Check budget before execution
        budget_status = self.budget_tracker.check_budget_safe(self.budget_tracker.project_id)
        if not budget_status['allowed']:
            raise ValueError(f"Budget exceeded: {budget_status['message']}")
        
        # Execute agent
        try:
            # Try synchronous chat first
            if hasattr(self.agent, 'chat'):
                logger.info(f"🚀 Starting agent execution ({self.role})")
                logger.debug(f"Input message: {message}")
                response = self.agent.chat(message, **kwargs)
                logger.debug(f"Agent raw response: {response}")
                logger.info("✅ Agent execution completed")
            elif hasattr(self.agent, 'run'):
                # Fallback to run (async)
                async def async_run():
                    return await self.agent.run(user_msg=message, max_iterations=100, **kwargs)
                
                logger.info(f"🚀 Starting agent execution (async {self.role})")
                response = asyncio.run(async_run())
                logger.info("✅ Agent execution completed")
            else:
                raise AttributeError(f"Agent {type(self.agent)} has neither 'run' nor 'chat' method")
        except Exception as e:
            logger.error(f"Error during agent execution: {e}")
            raise
        
        # Track budget - try to extract from response
        try:
            # LlamaIndex agents may store response in different places
            if hasattr(self.agent, 'chat_history') and self.agent.chat_history:
                last_message = self.agent.chat_history[-1]
                if hasattr(last_message, 'response'):
                    self._budget_callback(last_message.response)
            elif hasattr(response, 'response'):
                self._budget_callback(response.response)
            else:
                self._budget_callback(response)
        except Exception as e:
            logger.debug(f"Budget tracking skipped: {e}")
        
        return str(response)
    
    def stream_chat(self, message: str, **kwargs):
        """
        Stream chat with the agent
        
        Args:
            message: User message
            **kwargs: Additional arguments for agent stream chat
        
        Yields:
            Response chunks
        """
        # Check budget before execution
        budget_status = self.budget_tracker.check_budget(self.budget_tracker.project_id)
        if not budget_status['allowed']:
            raise ValueError(f"Budget exceeded: {budget_status['message']}")
        
        # Stream agent response
        for chunk in self.agent.stream_chat(message, **kwargs):
            yield chunk
