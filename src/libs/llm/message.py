"""
Message data structure for LLM conversations.

Provides type-safe message construction for text and multimodal inputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.libs.llm.content_block import (
    BlockType,
    ContentBlock,
    TextBlock,
    ImageBlock,
    AudioBlock,
    VideoBlock,
)


@dataclass
class Message:
    """
    Message in a conversation.

    Attributes:
        role: Message role ("system", "user", "assistant").
        content: List of content blocks (text, image, etc.).

    Usage:
        # Text message
        Message(role="user", content=[TextBlock(text="Hello")])

        # Multimodal message
        Message(role="user", content=[
            TextBlock(text="Describe this image"),
            ImageBlock(image="path/to/image.png"),
        ])
    """
    role: str
    content: list[ContentBlock]

    def __init__(self, role: str, content: list[ContentBlock] | list[dict] | str):
        """
        Initialize message.

        Args:
            role: Message role ("system", "user", "assistant").
            content: Content can be:
                - str: Pure text (auto-converted to TextBlock)
                - list[ContentBlock]: List of content blocks
                - list[dict]: List of dictionaries (OpenAI format)
        """
        self.role = role

        if isinstance(content, str):
            # Pure text, convert to TextBlock
            self.content = [TextBlock(text=content)]
        elif isinstance(content, list):
            if len(content) > 0 and isinstance(content[0], ContentBlock):
                # Already ContentBlock list
                self.content = content
            elif len(content) > 0 and isinstance(content[0], dict):
                # Dictionary list, convert to ContentBlock
                self.content = self._parse_dict_content(content)
            else:
                self.content = content
        else:
            raise ValueError(f"Invalid content type: {type(content)}")

    @staticmethod
    def _parse_dict_content(content_list: list[dict]) -> list[ContentBlock]:
        """Parse dictionary content to ContentBlock list."""
        blocks = []
        for item in content_list:
            block_type = item.get("type", "")

            if block_type == "text":
                blocks.append(TextBlock(text=item["text"]))
            elif block_type == "image":
                blocks.append(ImageBlock(
                    image=item.get("image", ""),
                    mime_type=item.get("mime_type", "image/png"),
                ))
            elif block_type == "image_url":
                # OpenAI format
                url = item.get("image_url", {}).get("url", "")
                blocks.append(ImageBlock(image=url))
            elif block_type == "audio":
                blocks.append(AudioBlock(
                    audio=item.get("audio", ""),
                    mime_type=item.get("mime_type", "audio/mp3"),
                ))
            elif block_type == "video":
                blocks.append(VideoBlock(
                    video=item.get("video", ""),
                    mime_type=item.get("mime_type", "video/mp4"),
                ))
            else:
                # Unknown type, preserve as dict
                blocks.append(item)

        return blocks

    def to_dict(self) -> dict[str, Any]:
        """
        Convert to dictionary format (OpenAI compatible).

        Returns:
            Dictionary with "role" and "content" keys.
        """
        content_list = []
        for block in self.content:
            if isinstance(block, ContentBlock):
                content_list.append(block.to_dict())
            elif isinstance(block, dict):
                content_list.append(block)

        # Simplify if single text block
        if len(content_list) == 1 and content_list[0].get("type") == "text":
            return {"role": self.role, "content": content_list[0]["text"]}

        return {"role": self.role, "content": content_list}

    def has_images(self) -> bool:
        """Check if message contains image content."""
        for block in self.content:
            if isinstance(block, ContentBlock):
                if block.type in (BlockType.IMAGE, BlockType.IMAGE_URL):
                    return True
            elif isinstance(block, dict):
                block_type = block.get("type", "")
                if "image" in block_type:
                    return True
        return False

    def get_text(self) -> str:
        """Extract all text content from message."""
        texts = []
        for block in self.content:
            if isinstance(block, TextBlock):
                texts.append(block.text)
            elif isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text", ""))
        return " ".join(texts)
