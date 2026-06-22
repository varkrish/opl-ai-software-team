import sys
sys.path.append("/app/agent/src")
from llamaindex_crew.utils.llm_config import GenericLlamaLLM
from llama_index.core.llms import ChatMessage, MessageRole

llm = GenericLlamaLLM(
    model="gpt-4o-mini",
    api_key="sk-or-v1-fake",
    api_base="https://openrouter.ai/api/v1",
    max_tokens=5,
)
try:
    resp = llm.chat([ChatMessage(role=MessageRole.USER, content="ping")])
    print(resp)
except Exception as e:
    print(f"Error: {e}")
