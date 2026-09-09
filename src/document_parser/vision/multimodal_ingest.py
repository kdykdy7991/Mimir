"""
Local multimodal ingestion: OCR / Caption sub-chunks (Phase 5).

Trims toward the plan's local Qwen3.8 27B (OpenAI-compatible) with no external
paid VLM provider. Reuses the existing LLM capability model: vision is gated by
``llm.capabilities`` (via ``OpenAICompatibleLLM.VISION_MODELS``), and
``supports_vision`` must pass a one-shot real-image probe before structural use.

For each content image we produce two sub-chunk descriptors:
  * ``image_ocr``     — structured body text / Markdown table / LaTeX / order;
  * ``image_caption`` — chart trend, process relations, dominant visual meaning.

Failure semantics (plan §Phase-5):
  * a single bad response is skipped (caller logs a warning, other images go on);
  * refuse / prompt-echo / empty / invalid OCR are detected and treated as
    skipped, NOT as success.
"""

from __future__ import annotations

import base64
import warnings
from dataclasses import dataclass, field

from src.libs.llm.content_block import TextBlock
from src.libs.llm.message import Message

_OCR_PROMPT = (
    "请把图片中的正文原样转录为 Markdown。要求："
    "正文在上，Markdown 表格居中，LaTeX 公式用 $...$ 或 $$...$$，"
    "保持阅读顺序；不要编造，不要输出与图片无关的内容。"
)
_CAPTION_PROMPT = (
    "请用两三句中文概括这张图片的主要内容：图表趋势、流程图关系、"
    "以及最主要的视觉语义。不要编造数据，不要复述提示词。"
)

# --- refusal / echo / invalid markers --------------------------------------
_REFUSE_MARKERS = (
    "抱歉", "对不起", "无法", "不能", "拒绝", "作為AI", "作为一个AI", "作为AI",
    "i cannot", "i'm sorry", "i am sorry", "cannot assist", "抱歉，我无法",
    "not able to", "unable to",
)
_PROMPT_ECHO_MAX = 0.6  # share of response that repeats the prompt => treat as echo


@dataclass
class VisionSubChunk:
    """A produced ``image_ocr`` / ``image_caption`` sub-chunk descriptor."""
    content_type: str            # "image_ocr" | "image_caption"
    text: str
    metadata: dict[str, object] = field(default_factory=dict)

    def to_chunk_dict(self) -> dict[str, object]:
        return {"content_type": self.content_type, "text": self.text, "metadata": dict(self.metadata)}


def vision_supported(llm) -> bool:
    """Whether the LLM declares a vision capability (preserves model routing)."""
    caps = getattr(llm, "capabilities", None) or set()
    return "vision" in caps


def _tiny_probe_png() -> bytes:
    # Minimal valid 1x1 PNG (no external image dependency).
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M8AAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
    )


def supports_vision_probe(llm) -> bool:
    """One-shot real-image probe: ask the local model about a tiny image.

    Returns False (never raises) when the model is not vision-capable or the
    call fails; a genuine failure is surfaced via ``warnings.warn``.
    """
    if not vision_supported(llm):
        return False
    try:
        messages = [
            Message("user", [
                TextBlock("图片里是纯色。回答：ok"),
                _image_block(_tiny_probe_png()),
            ]).to_dict(),
        ]
        out = llm.chat(messages, temperature=0.0, max_tokens=16)
        cleaned, ok = cleanup_vision_response(out or "", "caption")
        return ok
    except Exception as exc:  # noqa: BLE001
        warnings.warn(f"supports_vision probe failed: {exc}", stacklevel=2)
        return False


def _image_block(image_bytes: bytes, mime_type: str = "image/png"):
    from src.libs.llm.content_block import ImageBlock
    return ImageBlock(image=image_bytes, mime_type=mime_type)


def cleanup_vision_response(text: str, mode: str) -> tuple[str, bool]:
    """Return ``(clean_text, ok)``: False for refuse / echo / empty / invalid.

    A refusal, a near-full prompt echo, or an empty body means "no usable OCR",
    so callers must NOT mark that as success.
    """
    if not text:
        return "", False
    t = text.strip()
    low = t.lower()
    if any(m in low for m in _REFUSE_MARKERS):
        return "", False
    if mode == "ocr" and len(t) < 4:
        return "", False
    return t, True


def _echo_likely(response: str, prompt: str) -> bool:
    r = " ".join(response.split()).lower()
    if len(r) < 20:
        return False
    p = " ".join(prompt.split()).lower()
    # If the response is largely a verbatim prefix of the prompt, treat as echo.
    prefix = next((p[:i] for i in range(min(len(p), len(r)), 0, -1) if r.startswith(p[:i])), "")
    return len(prefix) >= _PROMPT_ECHO_MAX * len(p) if len(p) else False


def build_ocr_messages(image_bytes: bytes, mime_type: str = "image/png") -> list[dict]:
    return [Message("user", [TextBlock(_OCR_PROMPT), _image_block(image_bytes, mime_type)]).to_dict()]


def build_caption_messages(image_bytes: bytes, mime_type: str = "image/png") -> list[dict]:
    return [Message("user", [TextBlock(_CAPTION_PROMPT), _image_block(image_bytes, mime_type)]).to_dict()]


def produce_vision_subchunks(
    llm,
    *,
    image_bytes: bytes,
    mime_type: str,
    doc_id: str,
    chunk_index: int,
    page: int,
    image_id: str,
    model_version: str,
) -> list[VisionSubChunk]:
    """Produce ``image_ocr`` and ``image_caption`` sub-chunks for an image.

    Single-image failures are skipped (warning emitted), never marked success.
    Returns the list of valid sub-chunks (possibly empty).
    """
    if not vision_supported(llm):
        warnings.warn("model lacks vision support; skipping image sub-chunks", stacklevel=2)
        return []

    base_meta = {
        "parent_doc_id": doc_id,
        "parent_chunk_index": chunk_index,
        "page": page,
        "image_id": image_id,
        "model_version": model_version,
        "image_source_type": "image_content",
    }

    out: list[VisionSubChunk] = []
    for cfg in (
        {"kind": "image_ocr", "prompt": _OCR_PROMPT, "build": build_ocr_messages},
        {"kind": "image_caption", "prompt": _CAPTION_PROMPT, "build": build_caption_messages},
    ):
        try:
            raw = llm.chat(cfg["build"](image_bytes, mime_type), temperature=0.0, max_tokens=2048)
        except Exception as exc:  # noqa: BLE001
            warnings.warn(f"vision {cfg['kind']} failed for image {image_id}: {exc}", stacklevel=2)
            continue
        cleaned, ok = cleanup_vision_response(raw or "", cfg["kind"])
        if not ok or _echo_likely(cleaned, cfg["prompt"]):
            warnings.warn(f"vision {cfg['kind']} invalid/refused for image {image_id}", stacklevel=2)
            continue
        meta = dict(base_meta)
        meta["content_type"] = cfg["kind"]
        out.append(VisionSubChunk(content_type=cfg["kind"], text=cleaned, metadata=meta))
    return out