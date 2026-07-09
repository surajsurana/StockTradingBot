"""
Mock-based unit tests for news/news_agent.py's call_claude() error handling.

Covers what happens when the Anthropic API call itself fails (bad key,
insufficient credit balance, rate limit, network issue) -- these should all
surface as a ClaudeAPIError with a clear, human-readable reason, not an
uncaught anthropic SDK exception or a silent bad result. This is what lets
run_daily.py / monitor_positions.py / monthly_review.py catch one specific
exception type and send a clear Telegram alert instead of crashing silently.

Run with:
    python test_claude_api_error.py
"""

import unittest
from unittest.mock import patch, MagicMock

import anthropic

from news.news_agent import call_claude, ClaudeAPIError


class TestCallClaudeErrorHandling(unittest.TestCase):
    @patch("anthropic.Anthropic")
    def test_insufficient_credit_balance_raises_clear_claude_api_error(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = anthropic.AnthropicError(
            "Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', "
            "'message': 'Your credit balance is too low to access the Claude API. Please go "
            "to Plans & Billing to upgrade or purchase credits.'}}"
        )
        mock_anthropic_cls.return_value = mock_client

        with self.assertRaises(ClaudeAPIError) as ctx:
            call_claude("some prompt", api_key="fake-key")

        message = str(ctx.exception)
        self.assertIn("insufficient credit balance", message.lower())
        self.assertIn("console.anthropic.com/settings/billing", message)

    @patch("anthropic.Anthropic")
    def test_other_api_failure_raises_generic_claude_api_error(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = anthropic.AnthropicError("connection reset")
        mock_anthropic_cls.return_value = mock_client

        with self.assertRaises(ClaudeAPIError) as ctx:
            call_claude("some prompt", api_key="fake-key")

        message = str(ctx.exception)
        self.assertIn("Could not reach Claude", message)
        self.assertIn("connection reset", message)
        self.assertNotIn("credit balance", message.lower())

    @patch("anthropic.Anthropic")
    def test_successful_call_still_returns_text(self, mock_anthropic_cls):
        mock_block = MagicMock()
        mock_block.type = "text"
        mock_block.text = "SENTIMENT: neutral\nCONFIDENCE: 0.5\nREASONING: fine"

        mock_response = MagicMock()
        mock_response.content = [mock_block]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        mock_anthropic_cls.return_value = mock_client

        result = call_claude("some prompt", api_key="fake-key")
        self.assertIn("SENTIMENT: neutral", result)


if __name__ == "__main__":
    unittest.main()
