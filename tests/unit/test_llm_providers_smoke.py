"""
Smoke tests for LLM providers via ``OpenAICompatibleLLM``.

After the LLM refactor (``34659ce`` + ``a85bcf1``), all five
historical providers (openai, deepseek, minimax, mimo, vllm) route
to a single ``OpenAICompatibleLLM`` class — they differ only in
the ``api_key`` and ``base_url`` fields of their settings.

This file therefore focuses on:

* Factory routing — each provider name resolves to the unified
  class, not the legacy per-provider classes.
* ``OpenAICompatibleLLM`` behaviour — chat call signature,
  parameter passthrough (temperature / max_tokens), base_url
  passthrough, and the two error-mapping cases
  (``LLMAuthenticationError``, ``LLMConnectionError``) that the
  old per-provider tests covered.

Tests use mock HTTP to avoid real network calls.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.core.settings import LLMSettings
from src.libs.llm.base_llm import (
    LLMAuthenticationError,
    LLMConnectionError,
    LLMError,
)
from src.libs.llm.llm_factory import LLMFactory
from src.libs.llm.providers.openai_compatible import OpenAICompatibleLLM


# ---------------------------------------------------------------------------
# Mock response helper
# ---------------------------------------------------------------------------

def _mock_chat_response(content: str = "Hello from mock!") -> MagicMock:
    """Create a mock chat completion response."""
    mock_message = MagicMock()
    mock_message.content = content

    mock_choice = MagicMock()
    mock_choice.message = mock_message

    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    return mock_response


# All five legacy provider names route to the unified class.
PROVIDER_NAMES = ["openai", "deepseek", "minimax", "mimo", "vllm"]


# ---------------------------------------------------------------------------
# Tests: Factory routing for all providers
# ---------------------------------------------------------------------------

class TestProviderRouting:
    """Every supported provider resolves to ``OpenAICompatibleLLM``."""

    @pytest.mark.parametrize("provider", PROVIDER_NAMES)
    def test_factory_routes_to_openai_compatible(self, provider):
        settings = LLMSettings(
            provider=provider,
            api_key="test-key",
            model="some-model",
        )
        llm = LLMFactory.create(settings)
        assert isinstance(llm, OpenAICompatibleLLM)
        assert llm.__class__.__name__ == "OpenAICompatibleLLM"


# ---------------------------------------------------------------------------
# Tests: OpenAICompatibleLLM behaviour
# ---------------------------------------------------------------------------

class TestOpenAICompatibleLLM:
    """Unified tests for the OpenAI-compatible LLM class."""

    @patch("src.libs.llm.providers.openai_compatible.OpenAI")
    def test_chat_returns_content(self, mock_openai_cls):
        """chat() returns the first choice's message content."""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = _mock_chat_response("Hi!")

        settings = LLMSettings(
            provider="openai", api_key="test-key", model="gpt-4",
        )
        llm = OpenAICompatibleLLM(settings)
        result = llm.chat([{"role": "user", "content": "Hello"}])
        assert result == "Hi!"

    @patch("src.libs.llm.providers.openai_compatible.OpenAI")
    def test_chat_passes_parameters(self, mock_openai_cls):
        """chat() passes temperature + max_tokens to the OpenAI client."""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = _mock_chat_response()

        settings = LLMSettings(
            provider="openai",
            api_key="test-key",
            model="gpt-4",
            temperature=0.7,
            max_tokens=512,
        )
        llm = OpenAICompatibleLLM(settings)
        llm.chat([{"role": "user", "content": "Hello"}])

        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["model"] == "gpt-4"
        assert call_kwargs["temperature"] == 0.7
        assert call_kwargs["max_tokens"] == 512

    @patch("src.libs.llm.providers.openai_compatible.OpenAI")
    def test_chat_uses_custom_base_url(self, mock_openai_cls):
        """Custom base_url is forwarded to the OpenAI client."""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        settings = LLMSettings(
            provider="deepseek",
            api_key="test-key",
            model="deepseek-chat",
            base_url="https://api.deepseek.com/v1",
        )
        OpenAICompatibleLLM(settings)

        # OpenAI(api_key=..., base_url=...) — verify base_url
        # reaches the SDK client.
        _, kwargs = mock_openai_cls.call_args
        assert kwargs["base_url"] == "https://api.deepseek.com/v1"

    @patch("src.libs.llm.providers.openai_compatible.OpenAI")
    def test_default_base_url_is_openai(self, mock_openai_cls):
        """When no base_url is configured, fall back to OpenAI's."""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        settings = LLMSettings(
            provider="openai", api_key="test-key", model="gpt-4",
            base_url="",  # empty → default
        )
        OpenAICompatibleLLM(settings)

        _, kwargs = mock_openai_cls.call_args
        assert kwargs["base_url"] == "https://api.openai.com/v1"

    @patch("src.libs.llm.providers.openai_compatible.OpenAI")
    def test_api_key_passed_through(self, mock_openai_cls):
        """api_key from settings reaches the OpenAI client."""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        settings = LLMSettings(
            provider="openai", api_key="sk-abc-123", model="gpt-4",
        )
        OpenAICompatibleLLM(settings)

        _, kwargs = mock_openai_cls.call_args
        assert kwargs["api_key"] == "sk-abc-123"

    @pytest.mark.parametrize("provider", PROVIDER_NAMES)
    @patch("src.libs.llm.providers.openai_compatible.OpenAI")
    def test_per_provider_base_url_passthrough(
        self, mock_openai_cls, provider,
    ):
        """Each provider's base_url is forwarded to the client."""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        url = f"https://api.{provider}.example/v1"
        settings = LLMSettings(
            provider=provider,
            api_key="test-key",
            model="some-model",
            base_url=url,
        )
        OpenAICompatibleLLM(settings)

        _, kwargs = mock_openai_cls.call_args
        assert kwargs["base_url"] == url

    @patch("src.libs.llm.providers.openai_compatible.OpenAI")
    def test_vllm_works_with_empty_api_key(self, mock_openai_cls):
        """vLLM-style local server can be created with placeholder key."""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        settings = LLMSettings(
            provider="vllm",
            api_key="not-needed",
            model="local-model",
            base_url="http://localhost:8000/v1",
        )
        llm = OpenAICompatibleLLM(settings)
        assert llm.__class__.__name__ == "OpenAICompatibleLLM"


# ---------------------------------------------------------------------------
# Tests: Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    """The class maps SDK exceptions to project-specific error types."""

    @patch("src.libs.llm.providers.openai_compatible.OpenAI")
    def test_auth_error(self, mock_openai_cls):
        """401 / auth failures surface as ``LLMAuthenticationError``."""
        # Simulate an AuthenticationError from openai SDK.
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        # Use a generic exception subclass that the LLM module
        # recognises. The class catches ``openai.AuthenticationError``
        # by isinstance, so we mock at the call site.
        from openai import AuthenticationError

        mock_client.chat.completions.create.side_effect = AuthenticationError(
            "invalid api key",
            response=MagicMock(status_code=401),
            body=None,
        )

        settings = LLMSettings(provider="openai", api_key="bad", model="gpt-4")
        llm = OpenAICompatibleLLM(settings)
        with pytest.raises(LLMAuthenticationError):
            llm.chat([{"role": "user", "content": "Hello"}])

    @patch("src.libs.llm.providers.openai_compatible.OpenAI")
    def test_connection_error(self, mock_openai_cls):
        """Connection failures surface as ``LLMConnectionError``."""
        from openai import APIConnectionError

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.side_effect = APIConnectionError(
            request=MagicMock(),
        )

        settings = LLMSettings(
            provider="openai", api_key="test-key", model="gpt-4",
        )
        llm = OpenAICompatibleLLM(settings)
        with pytest.raises(LLMConnectionError):
            llm.chat([{"role": "user", "content": "Hello"}])
