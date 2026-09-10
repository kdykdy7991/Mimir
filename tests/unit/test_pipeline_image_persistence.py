"""Image-bytes persistence contract (Phase 5/7 rework).

Proves the docreader image path does not drop bytes: the adapter carries raw
bytes on the ImageRef, and the pipeline's ``_register_images`` persists them to
ImageStorage and rewrites the image ``path`` to a real file that the vision
transform can read (and clears inline bytes before chunking).
"""

from __future__ import annotations

from pathlib import Path

from src.core.types import Document
from src.document_parser.adapters import ParsedDocumentAdapter
from src.document_parser.types import ParsedDocument, ParsedImage
from src.ingestion.pipeline import IngestionPipeline

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 20


def _fake_pipeline(image_storage) -> IngestionPipeline:
    # Only _register_images (and helpers) are exercised; the rest are None.
    return IngestionPipeline(
        loader=None,
        chunker=None,
        transforms=[],
        batch_processor=None,
        vector_upserter=None,
        bm25_indexer=None,
        file_integrity=None,
        image_storage=image_storage,
    )


def test_adapter_carries_image_bytes_through(tmp_path):
    parsed = ParsedDocument(
        markdown="# doc\n\n![img](x)\n",
        images=[
            ParsedImage(
                filename="a.png",
                original_ref="img_1",
                mime_type="image/png",
                data=PNG_BYTES,
                page=1,
                is_original=True,
            ),
        ],
        metadata={"doc_id": "doc1"},
    )
    doc: Document = ParsedDocumentAdapter().to_document(parsed)
    dict_ref = doc.metadata["images"][0]
    assert dict_ref["path"] == "a.png"          # provisional filename
    assert dict_ref["data"] == PNG_BYTES         # bytes NOT dropped
    assert doc.images[0].data == PNG_BYTES        # structural ref carries bytes
    assert doc.images[0].mime_type == "image/png"


def test_register_images_persists_bytes_and_rewrites_paths(tmp_path):
    from src.ingestion.storage.image_storage import ImageStorage

    storage = ImageStorage(
        db_path=str(tmp_path / "img.db"),
        base_dir=str(tmp_path / "images"),
    )
    pipeline = _fake_pipeline(storage)
    parsed = ParsedDocument(
        markdown="# doc",
        images=[ParsedImage(filename="a.png", original_ref="img_1",
                            mime_type="image/png", data=PNG_BYTES, page=1, is_original=True)],
        metadata={"doc_id": "doc1"},
    )
    doc = ParsedDocumentAdapter().to_document(parsed)

    n = pipeline._register_images(doc, "coll")

    assert n == 1
    dict_ref = doc.metadata["images"][0]
    real = dict_ref["path"]
    assert Path(real).is_file()                 # real path exists on disk
    assert Path(real).read_bytes() == PNG_BYTES
    assert "data" not in dict_ref               # inline bytes cleared before chunking
    # structural ImageRef updated + bytes cleared so chunk metadata stays lean
    assert doc.images[0].path == real
    assert doc.images[0].data is None
    # vision transform can read it by path
    assert Path(doc.images[0].path).read_bytes() == PNG_BYTES


def test_register_images_legacy_path_still_reads_real_file(tmp_path):
    from src.ingestion.storage.image_storage import ImageStorage

    storage = ImageStorage(
        db_path=str(tmp_path / "img.db"), base_dir=str(tmp_path / "images"),
    )
    pipeline = _fake_pipeline(storage)
    real_file = tmp_path / "legacy.png"
    real_file.write_bytes(PNG_BYTES)
    doc = Document(
        id="d", text="# x",
        metadata={"images": [{"id": "img1", "path": str(real_file)}]},
    )
    n = pipeline._register_images(doc, "coll")
    assert n == 1
    assert Path(doc.metadata["images"][0]["path"]).read_bytes() == PNG_BYTES