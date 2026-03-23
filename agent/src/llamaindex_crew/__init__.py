"""
LlamaIndex-based AI Software Development Crew
Migration from CrewAI to LlamaIndex framework
"""
import os
import logging as _logging

# Disable HuggingFace tokenizers internal parallelism to avoid deadlocks when the
# process is forked (e.g. Flask reloader, job threads). Must be set before any
# tokenizer/embedding code runs.
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

__version__ = "0.1.0"

# Force local HuggingFace embeddings globally at package import time
# so LlamaIndex never falls back to OpenAI embeddings.
try:
    from llama_index.core import Settings as _Settings
    from llama_index.embeddings.huggingface import HuggingFaceEmbedding as _HFE
    _Settings.embed_model = _HFE(model_name="BAAI/bge-small-en-v1.5")
except Exception as _e:
    _logging.getLogger(__name__).debug("Could not set global embed_model at import: %s", _e)
