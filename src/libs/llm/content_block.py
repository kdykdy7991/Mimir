"""
Content block types for multimodal LLM messages.

Defines type-safe content blocks for text, image, audio, and video inputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class BlockType(Enum):
    """Content block type enumeration."""
    TEXT = "text"
    IMAGE = "image"
    IMAGE_URL = "image_url"
    AUDIO = "audio"      # Reserved for future
    VIDEO = "video"      # Reserved for future


@dataclass
class ContentBlock:
    """
    Base content block.

    All content blocks inherit from this class.
    Use specific subclasses for type safety.
    """
    type: BlockType


@dataclass
class TextBlock(ContentBlock):
    """
    Text content block.

    Usage:
        TextBlock(text="Hello, world!")
    """
    text: str

    def __init__(self, text: str):
        self.type = BlockType.TEXT
        self.text = text

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary format (OpenAI compatible)."""
        return {"type": "text", "text": self.text}


@dataclass
class ImageBlock(ContentBlock):
    """
    Image content block.

    Supports file path, bytes, or base64 URL.

    Usage:
        ImageBlock(image="path/to/image.png")
        ImageBlock(image=b"...", mime_type="image/png")
        ImageBlock(image="data:image/png;base64,...")
    """
    image: str | bytes
    mime_type: str = "image/png"

    def __init__(self, image: str | bytes, mime_type: str = "image/png"):
        self.type = BlockType.IMAGE
        self.image = image
        self.mime_type = mime_type

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary format (OpenAI compatible)."""
        if isinstance(self.image, str):
            # Already a URL or base64 string
            url = self.image
        else:
            # Bytes, need to encode
            import base64
            encoded = base64.b64encode(self.image).decode("utf-8")
            url = f"data:{self.mime_type};base64,{encoded}"

        return {
            "type": "image_url",
            "image_url": {"url": url}
        }


@dataclass
class AudioBlock(ContentBlock):
    """
    Audio content block (reserved for future).

    Usage:
        AudioBlock(audio="path/to/audio.mp3")
    """
    audio: str | bytes
    mime_type: str = "audio/mp3"

    def __init__(self, audio: str | bytes, mime_type: str = "audio/mp3"):
        self.type = BlockType.AUDIO
        self.audio = audio
        self.mime_type = mime_type

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary format."""
        # Placeholder for future implementation
        raise NotImplementedError("AudioBlock is reserved for future use")


@dataclass
class VideoBlock(ContentBlock):
    """
    Video content block (reserved for future).

    Usage:
        VideoBlock(video="path/to/video.mp4")
    """
    video: str | bytes
    mime_type: str = "video/mp4"

    def __init__(self, video: str | bytes, mime_type: str = "video/mp4"):
        self.type = BlockType.VIDEO
        self.video = video
        self.mime_type = mime_type

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary format."""
        # Placeholder for future implementation
        raise NotImplementedError("VideoBlock is reserved for future use")
