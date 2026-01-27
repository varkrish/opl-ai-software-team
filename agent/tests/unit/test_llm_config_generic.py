import unittest
from unittest.mock import MagicMock, patch
from llama_index.core.llms import ChatMessage, MessageRole
from src.llamaindex_crew.utils.llm_config import GenericLlamaLLM
import httpx

class TestGenericLlamaLLM(unittest.TestCase):
    def setUp(self):
        self.llm = GenericLlamaLLM(
            model="test-model",
            api_key="test-key",
            api_base="https://api.test.com/v1",
            context_window=4096
        )

    def test_message_formatting_alternation(self):
        """Test that consecutive roles are merged and system role is handled for MaaS"""
        messages = [
            ChatMessage(role=MessageRole.SYSTEM, content="System 1"),
            ChatMessage(role=MessageRole.SYSTEM, content="System 2"),
            ChatMessage(role=MessageRole.USER, content="User 1"),
            ChatMessage(role=MessageRole.USER, content="User 2"),
            ChatMessage(role=MessageRole.ASSISTANT, content="Assistant 1"),
        ]
        
        # We need to access the internal formatting logic or mock the post request
        with patch('httpx.Client.post') as mock_post:
            mock_post.return_value = MagicMock(status_code=200, json=lambda: {"choices": [{"message": {"content": "resp"}}]})
            self.llm.chat(messages)
            
            # Check the payload sent to the API
            args, kwargs = mock_post.call_args
            sent_messages = kwargs['json']['messages']
            
            # Expected:
            # 1. System 1 + System 2 merged (first role can be system)
            # 2. User 1 + User 2 merged
            # 3. Assistant 1
            # 4. Forced User message at end (because last was assistant and not a tool call)
            
            self.assertEqual(len(sent_messages), 4)
            self.assertEqual(sent_messages[0]['role'], 'system')
            self.assertIn("System 1", sent_messages[0]['content'])
            self.assertIn("System 2", sent_messages[0]['content'])
            
            self.assertEqual(sent_messages[1]['role'], 'user')
            self.assertIn("User 1", sent_messages[1]['content'])
            self.assertIn("User 2", sent_messages[1]['content'])
            
            self.assertEqual(sent_messages[2]['role'], 'assistant')
            self.assertEqual(sent_messages[3]['role'], 'user')

    def test_system_role_conversion(self):
        """Test that system role in the middle is converted to user for MaaS compatibility"""
        messages = [
            ChatMessage(role=MessageRole.USER, content="User 1"),
            ChatMessage(role=MessageRole.SYSTEM, content="System 1"),
        ]
        
        with patch('httpx.Client.post') as mock_post:
            mock_post.return_value = MagicMock(status_code=200, json=lambda: {"choices": [{"message": {"content": "resp"}}]})
            self.llm.chat(messages)
            
            sent_messages = kwargs = mock_post.call_args[1]['json']['messages']
            self.assertEqual(sent_messages[1]['role'], 'user')
            self.assertIn("System 1", sent_messages[1]['content'])

    @patch('time.sleep', return_value=None)
    def test_retry_logic(self, mock_sleep):
        """Test that the LLM retries on connection errors"""
        messages = [ChatMessage(role=MessageRole.USER, content="Hi")]
        
        with patch('httpx.Client.post') as mock_post:
            # Fail twice, succeed on third attempt
            mock_post.side_effect = [
                httpx.RemoteProtocolError("Server disconnected"),
                httpx.ReadTimeout("Timeout"),
                MagicMock(status_code=200, json=lambda: {"choices": [{"message": {"content": "Success"}}]})
            ]
            
            response = self.llm.chat(messages)
            self.assertEqual(response.message.content, "Success")
            self.assertEqual(mock_post.call_count, 3)

if __name__ == '__main__':
    unittest.main()
