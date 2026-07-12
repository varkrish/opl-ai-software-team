import unittest
from unittest.mock import MagicMock, patch
from llama_index.core.llms import ChatMessage, MessageRole
from src.llamaindex_crew.utils.llm_config import GenericLlamaLLM, _LLM_MAX_RETRIES
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

    # ------------------------------------------------------------------
    # socket.timeout — OS-level SSL stall (the hard-to-catch case)
    # ------------------------------------------------------------------

    @patch('time.sleep', return_value=None)
    def test_socket_timeout_retried(self, mock_sleep):
        """
        socket.timeout is raised when the OS-level TCP/SSL socket read stalls.
        This is distinct from httpx.ReadTimeout and was previously NOT in the
        retryable exceptions list, causing the entire process to hang until
        the pytest session timeout killed it.
        """
        import socket
        messages = [ChatMessage(role=MessageRole.USER, content="Hi")]

        with patch('httpx.Client.post') as mock_post:
            mock_post.side_effect = [
                socket.timeout("SSL read timed out"),  # OS-level stall
                MagicMock(
                    status_code=200,
                    json=lambda: {"choices": [{"message": {"content": "recovered"}}]},
                ),
            ]
            response = self.llm.chat(messages)
            self.assertEqual(response.message.content, "recovered")
            self.assertEqual(mock_post.call_count, 2)

    @patch('time.sleep', return_value=None)
    def test_socket_timeout_exhausted_raises(self, mock_sleep):
        """When every attempt gets a socket.timeout, the error must propagate."""
        import socket
        messages = [ChatMessage(role=MessageRole.USER, content="Hi")]

        with patch('httpx.Client.post') as mock_post:
            mock_post.side_effect = socket.timeout("SSL read timed out")
            with self.assertRaises(socket.timeout):
                self.llm.chat(messages)
            self.assertEqual(mock_post.call_count, _LLM_MAX_RETRIES)  # transport error attempts

    @patch('time.sleep', return_value=None)
    def test_429_rate_limit_retries_until_success(self, mock_sleep):
        """HTTP 429 must retry with backoff instead of failing the job immediately."""
        from llamaindex_crew.utils.llm_config import _LLM_RATE_LIMIT_MAX_RETRIES

        messages = [ChatMessage(role=MessageRole.USER, content="Hi")]
        rate_body = (
            '{"error":{"message":"Rate limit exceeded. Limit resets at: 2026-07-12 22:53:04 UTC"}}'
        )

        with patch('httpx.Client.post') as mock_post:
            mock_post.side_effect = [
                MagicMock(status_code=429, text=rate_body, headers={}),
                MagicMock(status_code=429, text=rate_body, headers={}),
                MagicMock(
                    status_code=200,
                    json=lambda: {"choices": [{"message": {"content": "Success"}}]},
                ),
            ]
            response = self.llm.chat(messages)
            self.assertEqual(response.message.content, "Success")
            self.assertEqual(mock_post.call_count, 3)

    @patch('time.sleep', return_value=None)
    def test_429_honours_retry_after_header(self, mock_sleep):
        from llamaindex_crew.utils.llm_config import _retry_delay_seconds

        delay = _retry_delay_seconds(
            0,
            status_code=429,
            response_text="",
            retry_after="42",
        )
        self.assertGreaterEqual(delay, 42.0)
        self.assertLessEqual(delay, 45.0)

    @patch('time.sleep', return_value=None)
    def test_oserror_stall_retried(self, mock_sleep):
        """OSError (base of socket.timeout) from a stalled connection is retried."""
        messages = [ChatMessage(role=MessageRole.USER, content="Hi")]

        with patch('httpx.Client.post') as mock_post:
            mock_post.side_effect = [
                OSError("Connection reset by peer"),
                MagicMock(
                    status_code=200,
                    json=lambda: {"choices": [{"message": {"content": "ok"}}]},
                ),
            ]
            response = self.llm.chat(messages)
            self.assertEqual(response.message.content, "ok")

    # ------------------------------------------------------------------
    # 400 context-length errors: trim-and-retry up to max_retries, then raise
    # ------------------------------------------------------------------

    @patch('time.sleep', return_value=None)
    def test_400_context_too_large_retries_then_raises(self, mock_sleep):
        """
        A 400 response for context-length exceeded is retried with a trimmed
        prompt (up to max_retries times) and then raises if it never succeeds.
        This avoids wasting money on non-context errors while still recovering
        from context overflows when possible.
        """
        messages = [ChatMessage(role=MessageRole.USER, content="Hi " * 500)]
        bad_response = MagicMock(
            status_code=400,
            text='{"error": {"message": "input tokens 8193 exceeds context 8192"}}',
        )
        bad_response.raise_for_status.side_effect = Exception("400 Bad Request")

        with patch('httpx.Client.post') as mock_post:
            mock_post.return_value = bad_response
            with self.assertRaises(Exception):
                self.llm.chat(messages)
            # Must retry (trim-and-retry until max_retries exhausted)
            self.assertGreater(mock_post.call_count, 1)

    # ------------------------------------------------------------------
    # httpx.Timeout object must be used (not a bare float)
    # ------------------------------------------------------------------

    def test_httpx_timeout_object_used(self):
        """
        The chat() method must pass an httpx.Timeout object (not a bare float)
        so that read, connect, and write timeouts are independently bounded.
        A bare float sets ONLY the total timeout and cannot bound a stalled SSL read.
        """
        messages = [ChatMessage(role=MessageRole.USER, content="Hi")]
        captured = {}

        original_init = httpx.Client.__init__

        def capturing_init(self_client, **kwargs):
            captured['timeout'] = kwargs.get('timeout')
            original_init(self_client, **kwargs)

        with patch.object(httpx.Client, '__init__', capturing_init):
            with patch('httpx.Client.post') as mock_post:
                mock_post.return_value = MagicMock(
                    status_code=200,
                    json=lambda: {"choices": [{"message": {"content": "ok"}}]},
                )
                self.llm.chat(messages)

        timeout = captured.get('timeout')
        self.assertIsNotNone(timeout, "httpx.Client must receive a timeout argument")
        self.assertIsInstance(
            timeout, httpx.Timeout,
            f"timeout must be httpx.Timeout, got {type(timeout)}. "
            "A bare float cannot bound individual SSL read stalls.",
        )
        # Each dimension must be individually bounded
        self.assertIsNotNone(timeout.read, "read timeout must be set")
        self.assertIsNotNone(timeout.connect, "connect timeout must be set")
        self.assertLessEqual(timeout.read, 300, "read timeout must be ≤ 300s")


if __name__ == '__main__':
    unittest.main()
