"""
Product Owner Agent - Creates user stories from vision
Migrated from ProductOwnerCrew to LlamaIndex agent
"""
import logging
from pathlib import Path
from typing import Optional, Union
from .base_agent import BaseLlamaIndexAgent
from ..tools import FileWriterTool, BulkFileWriterTool, create_workspace_file_tools, append_tldr_tools
from ..tools.tool_loader import load_tools
from ..config import ConfigLoader
from ..utils.prompt_loader import load_prompt
from ..utils.document_indexer import DocumentIndexer
from ..utils.llm_config import get_supports_react
from ..utils.output_parser import (
    product_owner_format_instruction,
    simple_mode_format_instruction,
    write_files_from_response,
)

logger = logging.getLogger(__name__)


class ProductOwnerAgent:
    """Product Owner Agent for creating user stories"""
    
    def __init__(
        self,
        custom_backstory: Optional[str] = None,
        budget_tracker=None,
        document_indexer: Optional[DocumentIndexer] = None,
        workspace_path: Optional[Union[str, Path]] = None,
        config=None,
    ):
        """
        Initialize Product Owner Agent
        
        Args:
            custom_backstory: Optional custom backstory (from Meta Agent)
            budget_tracker: Optional budget tracker instance
            document_indexer: Optional document indexer for RAG
            workspace_path: When set, file tools write to this path (avoids thread-local/env issues).
            config: Optional SecretConfig for RAG top-k / limits.
        """
        self.document_indexer = document_indexer
        self.config = config
        self.workspace_path = Path(workspace_path) if workspace_path else None

        default_backstory = load_prompt(
            'product_owner/product_owner_backstory.txt',
            fallback="""You are a Product Owner.
Your goal is to maximize value for stakeholders by defining clear requirements.
You use Impact Mapping: Goal -> Actor -> Impact -> Deliverable.
You break down requests into User Stories with Acceptance Criteria using Gherkin."""
        )

        backstory = custom_backstory or default_backstory

        # Determine capability mode for the manager model
        self.supports_react = get_supports_react("manager")
        logger.info("ProductOwnerAgent: supports_react=%s", self.supports_react)

        if self.supports_react:
            if workspace_path is not None:
                ws_tools = create_workspace_file_tools(Path(workspace_path))
                tools = [ws_tools[0], ws_tools[1]]  # file_writer and bulk_file_writer
                append_tldr_tools(tools, Path(workspace_path))
            else:
                tools = [FileWriterTool, BulkFileWriterTool]
        else:
            # Simple mode: no file tools — output parser handles writes
            tools = []

        try:
            cfg = ConfigLoader.load()
            entries = cfg.tools.global_tools + cfg.tools.agent_tools.get("product_owner", [])
            extra_tools = load_tools(entries)
            tools.extend(extra_tools)
            if extra_tools:
                backstory += (
                    "\n\nYou have access to a skill_query tool. BEFORE writing user stories, "
                    "use it to search for relevant framework skills and patterns (e.g. 'Frappe app user stories', "
                    "'invoicing workflow') to understand the target platform's capabilities and constraints."
                )
            logger.info("ProductOwnerAgent: loaded %d extra tool(s) from config", len(extra_tools))
        except Exception:
            logger.warning("ProductOwnerAgent: failed to load extra tools — continuing with built-ins", exc_info=True)

        self.agent = BaseLlamaIndexAgent(
            role="Product Owner",
            goal="Define user requirements and create user stories",
            backstory=backstory,
            tools=tools,
            agent_type="manager",
            budget_tracker=budget_tracker,
            verbose=True
        )
    
    def create_user_stories(
        self,
        vision: str,
        context_digest: Optional[str] = None
    ) -> str:
        """
        Create user stories based on project vision
        
        Args:
            vision: Project vision/idea
            context_digest: Optional Project Context Digest from Meta Agent
        
        Returns:
            Result message
        """
        # Retrieve relevant context from RAG if available
        rag_context = ""
        if self.document_indexer and getattr(self.document_indexer, "has_index", False):
            try:
                from ..utils.rag_context import get_phase_rag_context
                rag_context = get_phase_rag_context(
                    self.document_indexer,
                    "product_owner",
                    self.config,
                    extra_query=f"Project vision: {vision[:800]}",
                )
            except Exception as e:
                logger.debug("RAG retrieval failed: %s", e)
        elif self.document_indexer:
            try:
                rag_results = self.document_indexer.query(
                    f"Project vision: {vision}. What are the requirements and user needs?",
                    top_k=6,
                )
                if rag_results:
                    rag_context = "\n\nRelevant context from project artifacts:\n" + "\n".join(rag_results)
            except Exception as e:
                logger.debug(f"RAG retrieval failed: {e}")
        
        # Load task prompt
        task_prompt = load_prompt(
            'product_owner/create_user_stories_task.txt',
            fallback="""Create User Stories based on the project vision.

User Vision: {vision}
Project Context Digest: {context_digest}
{rag_context}

Create user stories with acceptance criteria using Gherkin format.
Save to user_stories.md and feature files."""
        )
        
        # Format prompt with vision and context
        if context_digest:
            prompt = task_prompt.format(
                vision=vision,
                context_digest=context_digest,
                rag_context=rag_context
            )
        else:
            prompt = task_prompt.format(
                vision=vision,
                context_digest="",
                rag_context=rag_context
            )

        # Simple mode: append JSON format instruction so the weak model knows
        # exactly what output to produce; we write files manually below.
        if not self.supports_react:
            prompt += product_owner_format_instruction()

        # Execute agent
        response = self.agent.chat(prompt)
        response_str = str(response)

        from ..tools.file_tools import _resolve_workspace
        ws_path = self.workspace_path or _resolve_workspace()

        if not self.supports_react:
            # Simple mode: primary parse + write
            write_files_from_response(
                response_str,
                ws_path,
                raw_fallback_path="user_stories.md",
                label="ProductOwnerAgent",
            )
        else:
            # ReAct safety net: tools should write files; parse response if artifacts missing
            needs_fallback = (
                not (ws_path / "requirements.md").exists()
                or not (ws_path / "user_stories.md").exists()
                or not (ws_path / "features").exists()
                or not any((ws_path / "features").glob("*.feature"))
            )
            if needs_fallback:
                logger.info("ProductOwnerAgent: ReAct safety net — parsing response for missing artifacts")
                write_files_from_response(
                    response_str,
                    ws_path,
                    raw_fallback_path="user_stories.md",
                    label="ProductOwnerAgent-safetynet",
                )

        return response_str
    
    def run(self, vision: str, context_digest: Optional[str] = None) -> str:
        """
        Run the Product Owner agent workflow
        
        Args:
            vision: Project vision
            context_digest: Optional Project Context Digest
        
        Returns:
            Result message
        """
        return self.create_user_stories(vision, context_digest)
