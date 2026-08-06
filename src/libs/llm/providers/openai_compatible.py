"""
OpenAI Compatible LLM provider.

Unified implementation for all OpenAI-compatible APIs:
- OpenAI
- DeepSeek
- MiniMax
- Mimo
- vLLM
- Ollama (with OpenAI compatibility mode)
"""

from __future__ import annotations

from typing import Any

from src.libs.llm.base_llm import (
    BaseLLM,
    LLMConnectionError,
    LLMAuthenticationError,
    LLMError,
)

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # type: ignore


class OpenAICompatibleLLM(BaseLLM):
    """
    OpenAI Compatible LLM provider.

    Supports all APIs compatible with OpenAI's chat completions format.
    Handles both text-only and multimodal (text + image) inputs.
    """

    # Known vision-capable models.
    #
    # Add a model name here if the underlying server actually
    # supports image inputs (the model advertises vision
    # capability via its OpenAI-compatible /v1/chat/completions
    # endpoint). Use the *exact* model id the server returns
    # from /v1/models so the ``capabilities`` check matches.
    #
    # The capabilities check itself is exact-string, so model
    # name aliases (e.g. "qwen-vl-7b" vs "Qwen2-VL-7B-Instruct")
    # must all be listed.
    VISION_MODELS = {
        # OpenAI
        "gpt-4o", "gpt-4o-mini",
        "gpt-4-turbo", "gpt-4-vision-preview",
        # Local vLLM / NVIDIA
        "Qwen3.6-35B-A3B-NVFP4",
    }

    def __init__(self, settings: Any):
        """
        Initialize OpenAI Compatible LLM.

        Args:
            settings: LLMSettings with provider, model, api_key, base_url, etc.
        """
        if OpenAI is None:
            raise LLMError(
                "openai package is not installed. "
                "Install it with: pip install openai"
            )

        self.settings = settings
        self.model = settings.model
        self.client = OpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url or "https://api.openai.com/v1",
        )

    @property
    def capabilities(self) -> set[str]:
        """
        Declare model capabilities.

        Returns:
            {"text", "vision"} for vision-capable models,
            {"text"} for text-only models.
        """
        if self.model in self.VISION_MODELS:
            return {"text", "vision"}
        return {"text"}

    def chat(self, messages: list[dict], **kwargs: Any) -> str:
        """
        Call OpenAI-compatible chat API.

        Supports both text-only and multimodal messages:
            # Text-only
            [{"role": "user", "content": "Hello"}]

            # Multimodal
            [{"role": "user", "content": [
                {"type": "text", "text": "Describe this image"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
            ]}]

        Args:
            messages: List of message dicts.
            **kwargs: Additional parameters (temperature, max_tokens, etc.).

        Returns:
            str: The model's response content.

        Raises:
            LLMAuthenticationError: If API key is invalid.
            LLMConnectionError: If connection fails.
            LLMError: For other errors.
        """
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=kwargs.get("temperature", self.settings.temperature),
                max_tokens=kwargs.get("max_tokens", self.settings.max_tokens),
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            error_msg = str(e).lower()
            if "authentication" in error_msg or "api_key" in error_msg or "api key" in error_msg:
                raise LLMAuthenticationError(
                    f"OpenAI authentication failed: {e}"
                ) from e
            if "connection" in error_msg or "timeout" in error_msg:
                raise LLMConnectionError(
                    f"OpenAI connection failed: {e}"
                ) from e
            raise LLMError(f"OpenAI API error: {e}") from e
