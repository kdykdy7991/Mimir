"""
Unit tests for multimodal LLM capabilities.

Tests cover:
- CapabilityValidator
- BaseLLM capabilities
- chat() with image content
- UnsupportedCapabilityError
"""

from __future__ import annotations

import pytest

from src.core.settings import LLMSettings
from src.libs.llm.base_llm import BaseLLM, UnsupportedCapabilityError
from src.libs.llm.capability_validator import CapabilityValidator
from src.libs.llm.content_block import TextBlock, ImageBlock
from src.libs.llm.message import Message


# ---------------------------------------------------------------------------
# Fake LLM implementations for testing
# ---------------------------------------------------------------------------

class FakeTextOnlyLLM(BaseLLM):
    """Fake LLM that only supports text."""

    def __init__(self, settings: LLMSettings):
        super().__init__()
        self.settings = settings

    @property
    def capabilities(self) -> set[str]:
        return {"text"}

    def chat(self, messages: list[dict], **kwargs) -> str:
        return "Text only response"


class FakeVisionLLM(BaseLLM):
    """Fake LLM that supports text and vision."""

    def __init__(self, settings: LLMSettings):
        super().__init__()
        self.settings = settings

    @property
    def capabilities(self) -> set[str]:
        return {"text", "vision"}

    def chat(self, messages: list[dict], **kwargs) -> str:
        return "Vision response"


# ---------------------------------------------------------------------------
# Tests: CapabilityValidator
# ---------------------------------------------------------------------------

class TestCapabilityValidator:
    """Test CapabilityValidator."""

    def test_validate_text_message_passes(self):
        """Text message passes validation for text-only model."""
        validator = CapabilityValidator()
        messages = [{"role": "user", "content": "Hello"}]

        # Should not raise
        validator.validate_dict_messages(messages, {"text"})

    def test_validate_text_message_with_vision_model_passes(self):
        """Text message passes validation for vision model."""
        validator = CapabilityValidator()
        messages = [{"role": "user", "content": "Hello"}]

        # Should not raise
        validator.validate_dict_messages(messages, {"text", "vision"})

    def test_validate_image_message_fails_for_text_model(self):
        """Image message fails validation for text-only model."""
        validator = CapabilityValidator()
        messages = [{"role": "user", "content": [
            {"type": "text", "text": "Describe this"},
            {"type": "image_url", "image_url": {"url": "..."}}
        ]}]

        with pytest.raises(UnsupportedCapabilityError) as exc_info:
            validator.validate_dict_messages(messages, {"text"})

        assert "image_url" in str(exc_info.value)
        assert "vision" in str(exc_info.value)

    def test_validate_image_message_passes_for_vision_model(self):
        """Image message passes validation for vision model."""
        validator = CapabilityValidator()
        messages = [{"role": "user", "content": [
            {"type": "text", "text": "Describe this"},
            {"type": "image_url", "image_url": {"url": "..."}}
        ]}]

        # Should not raise
        validator.validate_dict_messages(messages, {"text", "vision"})

    def test_validate_with_message_objects(self):
        """Validate Message objects."""
        validator = CapabilityValidator()

        msg = Message(role="user", content=[
            TextBlock(text="Hello"),
        ])

        # Should not raise
        validator.validate([msg], {"text"})

    def test_validate_image_message_with_message_objects(self):
        """Image Message fails for text-only model."""
        validator = CapabilityValidator()

        msg = Message(role="user", content=[
            TextBlock(text="Describe this"),
            ImageBlock(image="path/to/image.png"),
        ])

        with pytest.raises(UnsupportedCapabilityError):
            validator.validate([msg], {"text"})


# ---------------------------------------------------------------------------
# Tests: BaseLLM capabilities
# ---------------------------------------------------------------------------

class TestBaseLLMCapabilities:
    """Test BaseLLM capabilities property."""

    def test_text_only_llm_capabilities(self):
        """Text-only LLM has correct capabilities."""
        settings = LLMSettings(provider="fake", model="fake-model")
        llm = FakeTextOnlyLLM(settings)

        assert llm.capabilities == {"text"}

    def test_vision_llm_capabilities(self):
        """Vision LLM has correct capabilities."""
        settings = LLMSettings(provider="fake", model="fake-model")
        llm = FakeVisionLLM(settings)

        assert llm.capabilities == {"text", "vision"}


# ---------------------------------------------------------------------------
# Tests: BaseLLM.validate()
# ---------------------------------------------------------------------------

class TestBaseLLMValidate:
    """Test BaseLLM.validate() method."""

    def test_validate_text_message_passes(self):
        """validate() passes for text message on text-only model."""
        settings = LLMSettings(provider="fake", model="fake-model")
        llm = FakeTextOnlyLLM(settings)

        messages = [{"role": "user", "content": "Hello"}]
        # Should not raise
        llm.validate(messages)

    def test_validate_image_message_fails(self):
        """validate() fails for image message on text-only model."""
        settings = LLMSettings(provider="fake", model="fake-model")
        llm = FakeTextOnlyLLM(settings)

        messages = [{"role": "user", "content": [
            {"type": "text", "text": "Describe this"},
            {"type": "image_url", "image_url": {"url": "..."}}
        ]}]

        with pytest.raises(UnsupportedCapabilityError):
            llm.validate(messages)

    def test_validate_image_message_passes_on_vision_model(self):
        """validate() passes for image message on vision model."""
        settings = LLMSettings(provider="fake", model="fake-model")
        llm = FakeVisionLLM(settings)

        messages = [{"role": "user", "content": [
            {"type": "text", "text": "Describe this"},
            {"type": "image_url", "image_url": {"url": "..."}}
        ]}]

        # Should not raise
        llm.validate(messages)


# ---------------------------------------------------------------------------
# Tests: chat() with image content
# ---------------------------------------------------------------------------

class TestChatWithImage:
    """Test unified chat() method with image content."""

    def test_chat_with_vision_model(self):
        """chat() works with image content on vision model."""
        settings = LLMSettings(provider="fake", model="fake-model")
        llm = FakeVisionLLM(settings)

        messages = [{"role": "user", "content": [
            {"type": "text", "text": "Describe this"},
            {"type": "image_url", "image_url": {"url": "path/to/image.png"}}
        ]}]
        result = llm.chat(messages)
        assert result == "Vision response"

    def test_validate_catches_image_on_text_model(self):
        """validate() catches image content on text-only model."""
        settings = LLMSettings(provider="fake", model="fake-model")
        llm = FakeTextOnlyLLM(settings)

        messages = [{"role": "user", "content": [
            {"type": "text", "text": "Describe this"},
            {"type": "image_url", "image_url": {"url": "path/to/image.png"}}
        ]}]

        with pytest.raises(UnsupportedCapabilityError):
            llm.validate(messages)


# ---------------------------------------------------------------------------
# Tests: UnsupportedCapabilityError
# ---------------------------------------------------------------------------

class TestUnsupportedCapabilityError:
    """Test UnsupportedCapabilityError exception."""

    def test_error_message(self):
        """Error message contains useful information."""
        error = UnsupportedCapabilityError(
            "Content type 'image_url' requires 'vision' capability"
        )

        assert "image_url" in str(error)
        assert "vision" in str(error)

    def test_error_is_exception(self):
        """UnsupportedCapabilityError is an Exception."""
        error = UnsupportedCapabilityError("test")
        assert isinstance(error, Exception)
