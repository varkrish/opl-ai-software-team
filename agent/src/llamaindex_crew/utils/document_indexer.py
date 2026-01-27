"""
Document Indexer for RAG capabilities
Indexes project artifacts for retrieval by agents
"""
import logging
from pathlib import Path
from typing import List, Optional
from llama_index.core import VectorStoreIndex, Document, Settings
from llama_index.core.node_parser import SimpleNodeParser
try:
    from llama_index.embeddings.huggingface import HuggingFaceEmbedding
except ImportError:
    HuggingFaceEmbedding = None
import os

logger = logging.getLogger(__name__)


class DocumentIndexer:
    """Indexes project artifacts for RAG retrieval"""
    
    def __init__(self, workspace_path: Path, project_id: str):
        """
        Initialize document indexer
        
        Args:
            workspace_path: Path to workspace directory
            project_id: Project identifier
        """
        self.workspace_path = workspace_path
        self.project_id = project_id
        self.index = None
        self.index_path = workspace_path / f"index_{project_id}"
        
        # Initialize embeddings
        try:
            if HuggingFaceEmbedding:
                logger.info("🏠 Using local HuggingFace embeddings")
                Settings.embed_model = HuggingFaceEmbedding(model_name="BAAI/bge-small-en-v1.5")
            else:
                logger.warning("llama-index-embeddings-huggingface not installed, falling back to default")
        except Exception as e:
            logger.warning(f"Could not initialize local embeddings: {e}")
    
    def index_artifacts(self, artifact_files: List[str]) -> None:
        """
        Index project artifacts
        
        Args:
            artifact_files: List of artifact file paths relative to workspace
        """
        documents = []
        
        for file_path in artifact_files:
            full_path = self.workspace_path / file_path
            if not full_path.exists():
                logger.warning(f"Artifact file not found: {file_path}")
                continue
            
            try:
                with open(full_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # Create document with metadata
                doc = Document(
                    text=content,
                    metadata={
                        'file_path': file_path,
                        'project_id': self.project_id,
                        'file_type': full_path.suffix
                    }
                )
                documents.append(doc)
                logger.debug(f"Indexed: {file_path}")
            except Exception as e:
                logger.warning(f"Could not index {file_path}: {e}")
        
        if documents:
            # Create or update index
            if self.index is None:
                self.index = VectorStoreIndex.from_documents(documents)
            else:
                # Add new documents to existing index
                for doc in documents:
                    self.index.insert(doc)
            
            logger.info(f"✅ Indexed {len(documents)} artifacts")
        else:
            logger.warning("No documents to index")
    
    def query(self, query_text: str, top_k: int = 3) -> List[str]:
        """
        Query the index for relevant context
        
        Args:
            query_text: Query text
            top_k: Number of results to return
        
        Returns:
            List of relevant text snippets
        """
        if self.index is None:
            logger.warning("Index not initialized, returning empty results")
            return []
        
        try:
            query_engine = self.index.as_query_engine(similarity_top_k=top_k)
            response = query_engine.query(query_text)
            
            # Extract source nodes
            results = []
            if hasattr(response, 'source_nodes'):
                for node in response.source_nodes[:top_k]:
                    results.append(node.text)
            elif hasattr(response, 'response'):
                results.append(str(response.response))
            else:
                results.append(str(response))
            
            return results
        except Exception as e:
            logger.error(f"Error querying index: {e}")
            return []
    
    def index_default_artifacts(self) -> None:
        """Index default project artifacts"""
        default_files = [
            "requirements.md",
            "user_stories.md",
            "design_spec.md",
            "tech_stack.md"
        ]
        
        existing_files = [f for f in default_files if (self.workspace_path / f).exists()]
        
        if existing_files:
            self.index_artifacts(existing_files)
        else:
            logger.info("No default artifacts found to index")
