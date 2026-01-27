"""
Product Owner Agent - Creates user stories from vision
Migrated from ProductOwnerCrew to LlamaIndex agent
"""
import logging
from typing import Dict, Optional
from .base_agent import BaseLlamaIndexAgent
from ..tools import FileWriterTool
from ..utils.prompt_loader import load_prompt
from ..utils.document_indexer import DocumentIndexer

logger = logging.getLogger(__name__)


class ProductOwnerAgent:
    """Product Owner Agent for creating user stories"""
    
    def __init__(
        self,
        custom_backstory: Optional[str] = None,
        budget_tracker=None,
        document_indexer: Optional[DocumentIndexer] = None
    ):
        """
        Initialize Product Owner Agent
        
        Args:
            custom_backstory: Optional custom backstory (from Meta Agent)
            budget_tracker: Optional budget tracker instance
            document_indexer: Optional document indexer for RAG
        """
        self.document_indexer = document_indexer
        default_backstory = load_prompt(
            'product_owner/product_owner_backstory.txt',
            fallback="""You are a Product Owner.
Your goal is to maximize value for stakeholders by defining clear requirements.
You use Impact Mapping: Goal -> Actor -> Impact -> Deliverable.
You break down requests into User Stories with Acceptance Criteria using Gherkin."""
        )
        
        backstory = custom_backstory or default_backstory
        
        self.agent = BaseLlamaIndexAgent(
            role="Product Owner",
            goal="Define user requirements and create user stories",
            backstory=backstory,
            tools=[FileWriterTool],
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
        if self.document_indexer:
            try:
                rag_results = self.document_indexer.query(
                    f"Project vision: {vision}. What are the requirements and user needs?",
                    top_k=2
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
        
        # Execute agent
        response = self.agent.chat(prompt)
        
        return str(response)
    
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
