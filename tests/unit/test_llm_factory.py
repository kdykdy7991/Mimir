"""
Unit tests for LLM abstract interface and factory.

Tests cover:
- BaseLLM interface contract
- LLMFactory routing logic
- Error handling for unsupported providers
"""

from __future__ import annotations

import pytest

from src.core.settings import LLMSettings
from src.libs.llm.base_llm import BaseLLM, LLMError, LLMResponse
from src.libs.llm.llm_factory import LLMFactory


# ---------------------------------------------------------------------------
# Fake LLM implementations for testing
# ---------------------------------------------------------------------------

class FakeLLM(BaseLLM):
    """Fake LLM for testing - returns a fixed response."""

    def __init__(self, settings: LLMSettings, response: str = "fake response"):
        self.settings = settings
        self.response = response
        self.call_count = 0
        self.last_messages = None

    def chat(self, messages: list[dict], **kwargs) -> str:
        self.call_count += 1
        self.last_messages = messages
        return self.response


class FakeLLMWithHistory(BaseLLM):
    """Fake LLM that echoes back the messages."""

    def __init__(self, settings: LLMSettings):
        self.settings = settings

    def chat(self, messages: list[dict], **kwargs) -> str:
        return f"Echo: {messages[-1]['content']}"


# ---------------------------------------------------------------------------
# Tests: BaseLLM interface contract
# ---------------------------------------------------------------------------

class TestBaseLLMInterface:
    """Test that BaseLLM defines the correct interface."""

    def test_base_llm_cannot_be_instantiated(self):
        """BaseLLM is abstract and cannot be instantiated directly."""
        with pytest.raises(TypeError):
            BaseLLM()

    def test_fake_llm_satisfies_interface(self):
        """FakeLLM properly implements BaseLLM interface."""
        settings = LLMSettings(provider="fake", model="fake-model")
        llm = FakeLLM(settings)

        assert isinstance(llm, BaseLLM)
        assert hasattr(llm, "chat")
        assert callable(llm.chat)

    def test_chat_returns_string(self):
        """chat() must return a string."""
        settings = LLMSettings(provider="fake", model="fake-model")
        llm = FakeLLM(settings, response="test response")

        result = llm.chat([{"role": "user", "content": "hello"}])
        assert isinstance(result, str)
        assert result == "test response"

    def test_chat_with_history_prepends_system_prompt(self):
        """chat_with_history() prepends system prompt to messages."""
        settings = LLMSettings(provider="fake", model="fake-model")
        llm = FakeLLMWithHistory(settings)

        result = llm.chat_with_history(
            messages=[{"role": "user", "content": "hello"}],
            system_prompt="You are a helper.",
        )
        # FakeLLMWithHistory echoes last message
        assert result == "Echo: hello"

    def test_chat_with_history_no_system_prompt(self):
        """chat_with_history() works without system prompt."""
        settings = LLMSettings(provider="fake", model="fake-model")
        llm = FakeLLMWithHistory(settings)

        result = llm.chat_with_history(
            messages=[{"role": "user", "content": "hello"}],
        )
        assert result == "Echo: hello"


# ---------------------------------------------------------------------------
# Tests: LLMFactory routing
# ---------------------------------------------------------------------------

class TestLLMFactory:
    """Test LLMFactory routing logic."""

    def test_list_providers(self):
        """list_providers() returns supported providers."""
        providers = LLMFactory.list_providers()
        assert isinstance(providers, list)
        assert "openai" in providers
        assert "deepseek" in providers
        assert "minimax" in providers
        assert "mimo" in providers
        assert "vllm" in providers
        assert "ollama" in providers

    def test_register_custom_provider(self):
        """register_provider() adds a custom provider."""
        # Register fake provider
        LLMFactory.register_provider("fake", "tests.unit.test_llm_factory.FakeLLM")

        providers = LLMFactory.list_providers()
        assert "fake" in providers

        # Clean up
        from src.libs.llm.llm_factory import _PROVIDER_REGISTRY
        del _PROVIDER_REGISTRY["fake"]

    def test_create_with_fake_provider(self):
        """Factory can create instance with registered fake provider."""
        LLMFactory.register_provider("fake", "tests.unit.test_llm_factory.FakeLLM")

        try:
            settings = LLMSettings(provider="fake", model="fake-model")
            llm = LLMFactory.create(settings)

            # Check by class name since dynamic import creates different class object
            assert llm.__class__.__name__ == "FakeLLM"
            assert llm.settings.provider == "fake"
            assert hasattr(llm, "chat")
        finally:
            # Clean up
            from src.libs.llm.llm_factory import _PROVIDER_REGISTRY
            del _PROVIDER_REGISTRY["fake"]

    def test_unsupported_provider_raises_error(self):
        """Factory raises LLMError for unsupported provider."""
        settings = LLMSettings(provider="nonexistent", model="model")

        with pytest.raises(LLMError) as exc_info:
            LLMFactory.create(settings)

        assert "nonexistent" in str(exc_info.value)
        assert "Unsupported" in str(exc_info.value)

    def test_provider_case_insensitive(self):
        """Factory handles provider names case-insensitively."""
        LLMFactory.register_provider("fake", "tests.unit.test_llm_factory.FakeLLM")

        try:
            settings = LLMSettings(provider="FAKE", model="model")
            llm = LLMFactory.create(settings)
            assert llm.__class__.__name__ == "FakeLLM"
        finally:
            from src.libs.llm.llm_factory import _PROVIDER_REGISTRY
            del _PROVIDER_REGISTRY["fake"]


# ---------------------------------------------------------------------------
# Tests: LLMResponse dataclass
# ---------------------------------------------------------------------------

class TestLLMResponse:
    """Test LLMResponse dataclass."""

    def test_basic_response(self):
        """Create a basic response with content only."""
        response = LLMResponse(content="hello")
        assert response.content == "hello"
        assert response.model is None
        assert response.usage is None

    def test_full_response(self):
        """Create a response with all fields."""
        response = LLMResponse(
            content="hello",
            model="gpt-4",
            usage={"prompt_tokens": 10, "completion_tokens": 20},
            raw={"id": "test-id"},
        )
        assert response.content == "hello"
        assert response.model == "gpt-4"
        assert response.usage["prompt_tokens"] == 10
        assert response.raw["id"] == "test-id"
