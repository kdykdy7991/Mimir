"""Phase-5 transform assembly in the production pipeline (Phase 7 rework).

Proves that ``scripts.ingest.build_pipeline`` wires the multimodal
:class:`VisionIngestTransform` into the transform chain on the docreader path
instead of leaving it as an orphaned component.
"""

from __future__ import annotations

from scripts.ingest import build_pipeline
from src.core.settings import Settings


def _build(**overrides):
    settings = Settings()
    settings.ingestion.image_captioner.use_llm = overrides.pop("use_llm", False)
    return build_pipeline(
        settings=settings,
        data_dir=overrides.pop("data_dir", "/tmp/pp_vision_test"),
        collection="c",
        splitter=object(),
        embedding=object(),
        vector_store=object(),
        llm=overrides.pop("llm", None),
        document_parser=overrides.pop("document_parser", None),
    )


def test_docreader_path_assembles_vision_ingest_transform():
    llm = object()
    pipeline = _build(use_llm=True, llm=llm, document_parser=object())
    names = [t.name for t in pipeline.transforms]
    assert "vision_ingest" in names
    # the multimodal transform supersedes the legacy captioner on this path
    assert "image_captioner" not in names


def test_legacy_path_keeps_image_captioner():
    pipeline = _build(use_llm=True, llm=object(), document_parser=None)
    names = [t.name for t in pipeline.transforms]
    assert "image_captioner" in names
    assert "vision_ingest" not in names


def test_vision_requires_llm():
    pipeline = _build(use_llm=True, llm=None, document_parser=object())
    assert "vision_ingest" not in [t.name for t in pipeline.transforms]