"""
Base agent class for LlamaIndex agents
Provides common functionality for all agents including budget tracking and tool integration
"""
import asyncio
import inspect
import logging
import time
import nest_asyncio
from typing import List, Optional, Callable, Any, Dict
from llama_index.core.agent import ReActAgent, FunctionCallingAgentWorker, AgentRunner
from llama_index.core.llms import LLM
from llama_index.core.tools import FunctionTool

# Apply nest_asyncio to allow nested event loops
nest_asyncio.apply()

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
        
        from llama_index.core.callbacks import CallbackManager, TokenCountingHandler
        try:
            import tiktoken
            tokenizer = tiktoken.get_encoding("cl100k_base").encode
        except Exception:
            tokenizer = lambda x: [1] * max(1, len(str(x)) // 4)
            
        self.token_counter = TokenCountingHandler(tokenizer=tokenizer)
        
        # Create LLM with budget callback if not provided
        if llm is None:
            self.llm = get_llm_for_agent(agent_type)
        else:
            self.llm = llm
            
        self.llm.callback_manager = CallbackManager([self.token_counter])
        
        # Create agent instance
        self.agent = self._create_agent()
    
    def _create_agent(self):
        """Create LlamaIndex agent with tools and configuration"""
        # Use ReActAgent (available in all versions)
        # ReActAgent is instantiated directly with tools and llm
        
        if not self.tools:
            class SimpleAgent:
                def __init__(self, llm, system_prompt):
                    self.llm = llm
                    self.system_prompt = system_prompt
                    self.chat_history = []
                def reset(self):
                    self.chat_history.clear()
                def chat(self, message: str, **kwargs):
                    from llama_index.core.llms import ChatMessage
                    messages = [ChatMessage(role="system", content=self.system_prompt)]
                    messages.extend(self.chat_history)
                    messages.append(ChatMessage(role="user", content=message))
                    resp = self.llm.chat(messages)
                    self.chat_history.append(ChatMessage(role="user", content=message))
                    self.chat_history.append(ChatMessage(role="assistant", content=str(resp)))
                    return resp

            agent_context = f"""{self._build_system_prompt()}

You are an AI assistant. You have NO tools available.
Provide your response directly. Do NOT use Thought/Action/Observation formats.
Do NOT explain your reasoning. Output ONLY the structured format requested in the user message.
"""
            return SimpleAgent(self.llm, agent_context)

        system_prompt = self._build_system_prompt()
        instrumented = self._instrumented_tools()

        try:
            worker = FunctionCallingAgentWorker.from_tools(
                tools=instrumented,
                llm=self.llm,
                verbose=self.verbose,
                max_function_calls=50,
                system_prompt=system_prompt,
            )
            agent = AgentRunner(worker, callback_manager=self.llm.callback_manager)
            logger.info("Using FunctionCallingAgent for %s", self.role)
            return agent
        except Exception as e:
            logger.info("FunctionCallingAgent unavailable (%s), falling back to ReActAgent", e)

        agent_context = f"""{system_prompt}

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
            tools=instrumented,
            llm=self.llm,
            verbose=self.verbose,
            max_iterations=50,
            system_prompt=agent_context
        )
        logger.info("Using ReActAgent (text-based) for %s", self.role)
        return agent

    def _instrumented_tools(self) -> List[FunctionTool]:
        """Return tools wrapped with per-call stats recording."""
        return [self._wrap_tool_with_stats(t) for t in self.tools]

    def _wrap_tool_with_stats(self, tool: FunctionTool) -> FunctionTool:
        """Wrap a FunctionTool so each call is recorded in tool_usage."""
        tracker = self.budget_tracker
        agent_name = self.role
        tool_name = tool.metadata.name
        original_fn = tool._fn  # type: ignore[attr-defined]

        def _record(duration_ms: float) -> None:
            try:
                job_id = tracker.project_id
                if job_id and job_id != "default-project" and getattr(tracker, "job_db", None):
                    tracker.job_db.record_tool_usage(
                        job_id=job_id,
                        agent_name=agent_name,
                        tool_name=tool_name,
                        duration_ms=duration_ms,
                    )
            except Exception:
                pass

        if asyncio.iscoroutinefunction(original_fn):
            async def async_tracked(*args: Any, **kwargs: Any) -> Any:
                t0 = time.monotonic()
                result = await original_fn(*args, **kwargs)
                _record((time.monotonic() - t0) * 1000)
                return result
            tracked_fn: Any = async_tracked
        else:
            def sync_tracked(*args: Any, **kwargs: Any) -> Any:
                t0 = time.monotonic()
                result = original_fn(*args, **kwargs)
                _record((time.monotonic() - t0) * 1000)
                return result
            tracked_fn = sync_tracked

        return FunctionTool.from_defaults(
            fn=tracked_fn,
            name=tool_name,
            description=tool.metadata.description or "",
        )
    
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
    
    # (Budget tracking is now natively handled by TokenCountingHandler)
    
    def reset_chat(self) -> None:
        """Clear the agent's chat history so subsequent calls start fresh.
        Prevents context window overflow when making many sequential calls."""
        if hasattr(self.agent, 'reset'):
            self.agent.reset()
        elif hasattr(self.agent, 'chat_history'):
            self.agent.chat_history.clear()
        elif hasattr(self.agent, 'memory') and hasattr(self.agent.memory, 'reset'):
            self.agent.memory.reset()

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
            
        start_prompt_tokens = self.token_counter.prompt_llm_token_count
        start_completion_tokens = self.token_counter.completion_llm_token_count
        
        # Execute agent with retry for transient LLM parsing failures
        max_agent_retries = 3
        last_err = None
        for attempt in range(1, max_agent_retries + 1):
            try:
                if hasattr(self.agent, 'chat'):
                    logger.info(f"🚀 Starting agent execution ({self.role}) [attempt {attempt}/{max_agent_retries}]")
                    logger.debug(f"Input message: {message}")
                    response = self.agent.chat(message, **kwargs)
                    logger.debug(f"Agent raw response: {response}")
                    logger.info("✅ Agent execution completed")
                elif hasattr(self.agent, 'run'):
                    async def async_run():
                        return await self.agent.run(user_msg=message, max_iterations=100, **kwargs)
                    
                    logger.info(f"🚀 Starting agent execution (async {self.role}) [attempt {attempt}/{max_agent_retries}]")
                    response = asyncio.run(async_run())
                    logger.info("✅ Agent execution completed")
                else:
                    raise AttributeError(f"Agent {type(self.agent)} has neither 'run' nor 'chat' method")
                last_err = None
                break
            except (TypeError, AttributeError) as e:
                if "'NoneType'" in str(e) and attempt < max_agent_retries:
                    logger.warning(f"⚠️ Agent got empty/malformed LLM response (attempt {attempt}/{max_agent_retries}), retrying...")
                    import time; time.sleep(2 * attempt)
                    if hasattr(self.agent, 'memory'):
                        self.agent.memory.reset()
                    last_err = e
                    continue
                logger.error(f"Error during agent execution: {e}")
                raise
            except Exception as e:
                logger.error(f"Error during agent execution: {e}")
                raise
        if last_err is not None:
            raise last_err
        
        # Track budget using TokenCountingHandler
        try:
            end_prompt_tokens = self.token_counter.prompt_llm_token_count
            end_completion_tokens = self.token_counter.completion_llm_token_count
            
            input_tokens = end_prompt_tokens - start_prompt_tokens
            output_tokens = end_completion_tokens - start_completion_tokens
            
            if input_tokens > 0 or output_tokens > 0:
                self.budget_tracker.record_usage(
                    project_id=self.budget_tracker.project_id,
                    agent_name=self.role,
                    model=self.llm.metadata.model_name if hasattr(self.llm, 'metadata') else 'unknown',
                    input_tokens=input_tokens,
                    output_tokens=output_tokens
                )
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
