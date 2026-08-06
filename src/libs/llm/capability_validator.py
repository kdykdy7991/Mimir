"""
Capability validator for LLM requests.

Validates that input content types match model capabilities
before sending requests to the LLM provider.
"""

from __future__ import annotations

from src.libs.llm.content_block import BlockType, ContentBlock
from src.libs.llm.message import Message


class UnsupportedCapabilityError(Exception):
    """
    Raised when input content type requires a capability
    that the model does not support.

    Example:
        - Sending image to a text-only model
        - Sending audio to a model without audio support
    """
    pass


class CapabilityValidator:
    """
    LLM capability validator.

    Validates that input content types match model capabilities
    before sending requests to the LLM provider.

    This prevents unsupported input types from being sent to the model,
    providing clear error messages instead of cryptic API errors.
    """

    # Mapping: BlockType -> required capability
    CAPABILITY_MAP: dict[BlockType, str] = {
        BlockType.TEXT: "text",
        BlockType.IMAGE: "vision",
        BlockType.IMAGE_URL: "vision",
        BlockType.AUDIO: "audio",
        BlockType.VIDEO: "video",
    }

    def validate(self, messages: list[Message], capabilities: set[str]) -> None:
        """
        Validate that message content types match model capabilities.

        Args:
            messages: List of Message objects.
            capabilities: Model's supported capabilities (e.g., {"text", "vision"}).

        Raises:
            UnsupportedCapabilityError: If any content type requires
                a capability not in the model's capabilities.
        """
        for msg in messages:
            for block in msg.content:
                if isinstance(block, ContentBlock):
                    required = self.CAPABILITY_MAP.get(block.type)
                    if required and required not in capabilities:
                        raise UnsupportedCapabilityError(
                            f"Content type '{block.type.value}' requires "
                            f"'{required}' capability, but model only supports: "
                            f"{capabilities}"
                        )

    def validate_dict_messages(
        self,
        messages: list[dict],
        capabilities: set[str],
    ) -> None:
        """
        Validate dictionary-format messages (for backward compatibility).

        Args:
            messages: List of message dictionaries.
            capabilities: Model's supported capabilities.

        Raises:
            UnsupportedCapabilityError: If any content type requires
                a capability not in the model's capabilities.
        """
        for msg in messages:
            content = msg.get("content")

            # Pure text message
            if isinstance(content, str):
                if "text" not in capabilities:
                    raise UnsupportedCapabilityError(
                        f"Text content requires 'text' capability, "
                        f"but model only supports: {capabilities}"
                    )
                continue

            # Multimodal message
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue

                    block_type_str = block.get("type", "")

                    # Map string type to BlockType
                    try:
                        block_type = BlockType(block_type_str)
                    except ValueError:
                        # Unknown type, skip validation
                        continue

                    required = self.CAPABILITY_MAP.get(block_type)
                    if required and required not in capabilities:
                        raise UnsupportedCapabilityError(
                            f"Content type '{block_type_str}' requires "
                            f"'{required}' capability, but model only supports: "
                            f"{capabilities}"
                        )
