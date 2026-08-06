"""
LLM abstract base class.

Defines the unified interface for all LLM providers (Azure, OpenAI, Ollama, etc.).
Supports both text-only and multimodal (text + image) inputs via capability model.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from src.libs.llm.capability_validator import CapabilityValidator, UnsupportedCapabilityError
from src.libs.llm.content_block import BlockType, ContentBlock, TextBlock, ImageBlock
from src.libs.llm.message import Message


@dataclass
class LLMResponse:
    """Unified response object from LLM providers."""
    content: str
    model: str | None = None
    usage: dict[str, int] | None = None
    raw: Any = None  # Provider-specific raw response


class BaseLLM(ABC):
    """
    Abstract base class for LLM providers.

    All LLM implementations must inherit from this class
    and implement the `chat` method.

    Supports both text-only and multimodal inputs via capability model.
    """

    def __init__(self):
        self._validator = CapabilityValidator()

    @property
    def capabilities(self) -> set[str]:
        """
        Declare model capabilities.

        Subclasses should override this to declare supported capabilities.

        Examples:
            - Text-only model: {"text"}
            - Multimodal model: {"text", "vision"}
            - Future: {"text", "vision", "audio", "video"}

        Returns:
            Set of capability strings.
        """
        return {"text"}

    @abstractmethod
    def chat(self, messages: list[dict], **kwargs: Any) -> str:
        """
        Send a chat request to the LLM and return the response.

        Supports both text-only and multimodal messages:
            # Text-only
            [{"role": "user", "content": "Hello"}]

            # Multimodal
            [{"role": "user", "content": [
                {"type": "text", "text": "Describe this image"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
            ]}]

        Args:
            messages: List of message dicts with 'role' and 'content' keys.
            **kwargs: Additional provider-specific parameters
                      (e.g., temperature, max_tokens).

        Returns:
            str: The LLM's response content.

        Raises:
            LLMError: If the request fails.
        """
        pass

    def chat_with_history(
        self,
        messages: list[dict],
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> str:
        """
        Chat with optional system prompt prepended.

        Args:
            messages: Conversation messages.
            system_prompt: Optional system prompt to prepend.
            **kwargs: Additional parameters passed to chat().

        Returns:
            str: The LLM's response content.
        """
        full_messages = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)
        return self.chat(full_messages, **kwargs)

    def validate(self, messages: list[dict]) -> None:
        """
        Validate that message content types match model capabilities.

        Call this before chat() to catch capability mismatches early.

        Args:
            messages: List of message dicts.

        Raises:
            UnsupportedCapabilityError: If any content type requires
                a capability not supported by this model.
        """
        self._validator.validate_dict_messages(messages, self.capabilities)


class LLMError(Exception):
    """Base exception for LLM-related errors."""
    pass


class LLMConnectionError(LLMError):
    """Raised when connection to LLM provider fails."""
    pass


class LLMAuthenticationError(LLMError):
    """Raised when authentication with LLM provider fails."""
    pass


class LLMRateLimitError(LLMError):
    """Raised when rate limit is exceeded."""
    pass
