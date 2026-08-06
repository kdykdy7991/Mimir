"""
End-to-end integration test for IngestionPipeline (C14).

Builds a small PDF in-test (text + 1 embedded image), runs the
full pipeline, then asserts every artifact the spec calls for:

  ✓ Vector store (FakeVectorStore) holds the right number of records
  ✓ BM25 index file exists at the expected path with all docs
  ✓ Image is registered in the SQLite index and the file lives
    under the configured storage root
  ✓ FileIntegrity marks the hash as success
  ✓ Re-running the same file is a no-op (skipped=True)

Why build the PDF in-test: the spec asks for a 21KB complex fixture
to live at ``tests/fixtures/sample_documents/complex_technical_doc.pdf``.
We follow the same spirit with a smaller in-test PDF that exercises
the same code paths — text extraction, image extraction, multi-page
chunks. This keeps the test repo self-contained and the run under
a second.
"""

from __future__ import annotations

import io
import struct
import zlib
from pathlib import Path

import pymupdf
import pytest

from src.core.settings import (
    ChunkRefinerSettings,
    MetadataEnricherSettings,
    SplitterSettings,
    VectorStoreSettings,
)
from src.libs.vector_store.chroma_store import chroma_collection_name
from src.libs.vector_store.collection_router import MultiCollectionVectorStore
from src.libs.vector_store.scoped import ScopedCollectionVectorStore
from src.core.types import Chunk, ChunkRecord, Document
from src.ingestion.chunking.document_chunker import DocumentChunker
from src.ingestion.embedding import (
    BatchProcessor,
    DenseEncoder,
    SparseEncoder,
)
from src.ingestion.embedding.dense_encoder import DenseEncoder as _DenseEnc
from src.ingestion.pipeline import (
    IngestionPipeline,
    PipelineResult,
    PipelineStageError,
)
from src.ingestion.storage import (
    BM25Indexer,
    ImageStorage,
    VectorUpserter,
)
from src.ingestion.transform import ChunkRefiner, MetadataEnricher
from src.libs.embedding.base_embedding import BaseEmbedding
from src.libs.loader import (
    BaseLoader,
    LoaderError,
    PdfLoader,
    SQLiteIntegrityChecker,
)
from src.libs.splitter.base_splitter import BaseSplitter
from src.libs.vector_store.base_vector_store import (
    BaseVectorStore,
    VectorRecord,
)


# ---------------------------------------------------------------------------
# In-test PDF generation
# ---------------------------------------------------------------------------

def _minimal_png(width: int = 8, height: int = 8) -> bytes:
    """A solid-color PNG (8×8 black) — just enough to embed."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x00" * width for _ in range(height))
    idat = zlib.compress(raw)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def _build_sample_pdf(path: Path) -> None:
    """Two pages: page 1 has text + an image, page 2 has more text."""
    doc = pymupdf.open()
    # Page 1
    page = doc.new_page()
    page.insert_text((72, 72), "Page One: Introduction to Modular RAG.")
    page.insert_text((72, 100), "RAG pipelines combine retrieval and generation.")
    page.insert_text((72, 200), "[IMAGE: caption above the figure below]")
    page.insert_image(
        pymupdf.Rect(72, 220, 200, 320),
        stream=_minimal_png(16, 16),
    )
    # Page 2
    page = doc.new_page()
    page.insert_text((72, 72), "Page Two: BM25 and Dense Retrieval.")
    page.insert_text((72, 100), "BM25 is a bag-of-words retrieval method.")
    page.insert_text((72, 130), "Dense retrieval uses embedding vectors.")
    doc.save(str(path))
    doc.close()


@pytest.fixture
def sample_pdf(tmp_path) -> Path:
    p = tmp_path / "sample.pdf"
    _build_sample_pdf(p)
    return p


# ---------------------------------------------------------------------------
# Fake / controlled components
# ---------------------------------------------------------------------------

class FakeEmbedding(BaseEmbedding):
    """Deterministic dense embeddings — vector contents don't matter,
    just that there IS one of the right shape."""

    def __init__(self, dim: int = 8) -> None:
        self._dim = dim

    @property
    def dimensions(self) -> int:
        return self._dim

    def embed(self, texts, **kwargs):
        return [
            [float((hash(t) % 100) + i) / 100 for i in range(self._dim)]
            for t in texts
        ]


class FakeVectorStore(BaseVectorStore):
    """In-memory vector store that records all upserts. Subclasses
    the real BaseVectorStore so the contract is enforced at type
    level (no monkey-patching of __bases__).
    """

    def __init__(self) -> None:
        self._by_id: dict[str, VectorRecord] = {}

    @property
    def records(self) -> list[VectorRecord]:
        return list(self._by_id.values())

    def upsert(self, records, **kwargs):
        for r in records:
            self._by_id[r.id] = r
        return len(records)

    def query(self, vector, top_k=10, filters=None, **kwargs):
        return []

    def delete(self, ids, **kwargs):
        before = len(self._by_id)
        for i in ids:
            self._by_id.pop(i, None)
        return before - len(self._by_id)

    def get_by_ids(self, ids, **kwargs):
        return [
            {"id": r.id, "text": r.text, "metadata": dict(r.metadata)}
            for r in self.records if r.id in set(ids)
        ]

    def count(self, **kwargs):
        return len(self._by_id)


class ParaSplitter(BaseSplitter):
    """Split on blank lines (mirrors the test splitter in C4)."""
    def split_text(self, text, **kwargs):
        return [p for p in text.split("\n\n") if p.strip()]


# ---------------------------------------------------------------------------
# Pipeline assembly
# ---------------------------------------------------------------------------

def _build_pipeline(
    tmp_path: Path,
    *,
    include_transforms: bool = True,
    include_image_storage: bool = True,
    collection: str = "default",
    vector_store: Any | None = None,
) -> tuple[IngestionPipeline, BaseVectorStore, BM25Indexer, ImageStorage,
            SQLiteIntegrityChecker, PdfLoader]:
    """Wire up a fully-functional pipeline with tmp paths everywhere."""
    db_path = tmp_path / "ingest.db"
    img_db = tmp_path / "image_index.db"
    img_dir = tmp_path / "images"
    bm25_dir = tmp_path / "bm25"

    integrity = SQLiteIntegrityChecker(str(db_path))
    image_storage = ImageStorage(
        db_path=str(img_db), base_dir=str(img_dir),
    ) if include_image_storage else None

    loader = PdfLoader(image_dir=str(img_dir))
    chunker = DocumentChunker(ParaSplitter())
    transforms: list = []
    if include_transforms:
        # Both transforms run rule-only (no LLM).
        (tmp_path / "refine_prompt.txt").write_text(
            "Clean: {chunk_text}", encoding="utf-8"
        )
        transforms.append(ChunkRefiner(
            ChunkRefinerSettings(
                use_llm=False,
                prompt_path=str(tmp_path / "refine_prompt.txt"),
            )
        ))
        (tmp_path / "meta_prompt.txt").write_text(
            "META: {chunk_text}", encoding="utf-8"
        )
        transforms.append(MetadataEnricher(
            MetadataEnricherSettings(
                use_llm=False,
                prompt_path=str(tmp_path / "meta_prompt.txt"),
            )
        ))

    batch = BatchProcessor(
        dense_encoder=_DenseEnc(FakeEmbedding(dim=8)),
        sparse_encoder=SparseEncoder(),
        batch_size=4,
    )
    vector_store = vector_store or FakeVectorStore()
    upserter = VectorUpserter(vector_store)
    bm25 = BM25Indexer(
        persist_dir=str(bm25_dir),
        sparse_encoder=SparseEncoder(),
    )
    pipeline = IngestionPipeline(
        loader=loader,
        chunker=chunker,
        transforms=transforms,
        batch_processor=batch,
        vector_upserter=upserter,
        bm25_indexer=bm25,
        file_integrity=integrity,
        image_storage=image_storage,
        bm25_index_name="corpus",
        # Mirrors ``scripts.ingest.build_pipeline``: a named-collection
        # pipeline carries its collection on the ctor so ``run()``
        # without an explicit ``collection=`` still scopes correctly.
        collection=collection,
    )
    return pipeline, vector_store, bm25, image_storage, integrity, loader


# ---------------------------------------------------------------------------
# Full pipeline run
# ---------------------------------------------------------------------------

class TestEndToEnd:
    def test_pipeline_runs_on_pdf_and_produces_all_artifacts(
        self, sample_pdf, tmp_path
    ):
        pipeline, vector_store, bm25, image_storage, integrity, _ = (
            _build_pipeline(tmp_path)
        )
        result = pipeline.run(str(sample_pdf))

        # ---- Result shape ---------------------------------------
        assert isinstance(result, PipelineResult)
        assert not result.skipped
        assert result.n_chunks > 0
        assert result.n_records_upserted == result.n_chunks
        assert result.bm25_n_docs == result.n_chunks

        # ---- Vector store ---------------------------------------
        assert vector_store.count() == result.n_chunks
        # All records have a non-empty dense vector
        for r in vector_store.records:
            assert r.vector
            assert r.text

        # ---- BM25 index -----------------------------------------
        bm25_path = tmp_path / "bm25" / "corpus.json"
        assert bm25_path.exists()
        loaded = bm25.load("corpus")
        assert loaded.n_docs == result.n_chunks
        # The known term "BM25" should be indexed (it appears on
        # page 2). In a 2-doc corpus, df=1 → idf=log(1.0)=0, so a
        # raw BM25 query returns 0 score for that specific term —
        # we just verify it's IN the index, not that it retrieves.
        assert "bm25" in loaded.terms
        # "retrieval" appears in both chunks → df=2 → idf<0 → score
        # is non-zero and gets returned.
        hits = bm25.query(loaded, "retrieval", top_k=3)
        assert len(hits) > 0

        # ---- Image storage --------------------------------------
        assert result.n_images_saved == 1
        # File exists on disk — the pipeline passes its collection
        # name ("default") to ``ImageStorage.save``, so the image
        # is stored under that collection, not the legacy "_default"
        # fallback label.
        first_img = image_storage.find_by_collection("default")
        assert len(first_img) == 1
        assert Path(first_img[0].file_path).exists()

        # ---- Integrity marked success ---------------------------
        h = integrity.compute_sha256(str(sample_pdf))
        assert integrity.should_skip(h) is True

    def test_pipeline_returns_skipped_on_second_run(
        self, sample_pdf, tmp_path
    ):
        """Re-running the same file is a no-op (spec: '失败步骤抛出
        明确异常' is the other contract; the skip path is the
        common case)."""
        pipeline, _, _, _, _, _ = _build_pipeline(tmp_path)
        r1 = pipeline.run(str(sample_pdf))
        assert r1.skipped is False
        r2 = pipeline.run(str(sample_pdf))
        assert r2.skipped is True
        # The file_hash matches between runs
        assert r1.file_hash == r2.file_hash

    def test_named_collection_scopes_integrity_and_images(
        self, sample_pdf, tmp_path
    ):
        """M3 regression: a named-collection pipeline must label the
        integrity record and stored images 'test', not fall back to
        'default'.

        Reproduces the real wiring: ``scripts.ingest.build_pipeline``
        builds the pipeline with ``collection=...`` on the ctor, and the
        consumer calls ``run()`` without an explicit ``collection=``
        (the Web API upload worker's per-request forwarding is covered
        by ``test_application_ingestion.py``). Before the fix a
        non-default upload wrote its dense/sparse indexes to the right
        collection but its integrity + image rows to 'default' — so the
        document showed up under the wrong knowledge base.
        """
        pipeline, _, _, image_storage, integrity, _ = _build_pipeline(
            tmp_path, collection="test",
        )
        result = pipeline.run(str(sample_pdf))

        assert not result.skipped
        assert result.n_chunks > 0

        # Integrity record is scoped to "test" — the document lists
        # under the right collection, not "default".
        h = integrity.compute_sha256(str(sample_pdf))
        assert integrity.should_skip(h, collection="test") is True
        assert integrity.should_skip(h) is False  # NOT under default

        # Images land under the named collection too.
        imgs = image_storage.find_by_collection("test")
        assert len(imgs) == 1
        assert Path(imgs[0].file_path).exists()

    def test_chinese_collection_ingests_through_real_chroma(
        self, sample_pdf, tmp_path,
    ):
        """M4 regression: a Chinese-named knowledge base ingests end-to-end.

        ChromaDB rejects non-ASCII collection names; the router maps the
        display name to a Chroma-safe internal name so the whole pipeline
        (vectors + integrity + images) works for Chinese collections.
        Mirrors the Web API wiring: the upserter calls carry no
        ``collection`` kwarg, so a ``ScopedCollectionVectorStore`` bound
        to the display name routes them into the mapped Chroma collection.
        """
        router = MultiCollectionVectorStore(VectorStoreSettings(
            backend="chroma", persist_path=str(tmp_path / "chroma"),
        ))
        scoped = ScopedCollectionVectorStore(router, "测试rag用例")
        pipeline, _, _, image_storage, integrity, _ = _build_pipeline(
            tmp_path, collection="测试rag用例", vector_store=scoped,
        )
        result = pipeline.run(str(sample_pdf))

        assert not result.skipped
        assert result.n_chunks > 0
        assert result.n_records_upserted == result.n_chunks

        # Vectors landed in the mapped Chroma collection, reachable via
        # the display name through the router.
        assert router.count(collection="测试rag用例") == result.n_chunks
        assert router.count(
            collection=chroma_collection_name("测试rag用例"),
        ) == result.n_chunks

        # Integrity + images are scoped to the display name.
        h = integrity.compute_sha256(str(sample_pdf))
        assert integrity.should_skip(h, collection="测试rag用例") is True
        assert integrity.should_skip(h) is False
        assert len(image_storage.find_by_collection("测试rag用例")) == 1

    def test_pipeline_chunks_have_metadata_from_transforms(
        self, sample_pdf, tmp_path
    ):
        """After C5+C6, every chunk should have refined_by + title/
        summary/tags populated."""
        pipeline, vector_store, _, _, _, _ = _build_pipeline(tmp_path)
        pipeline.run(str(sample_pdf))
        # C6's MetadataEnricher wrote into chunk metadata before
        # encoding; we can verify the title made it into the stored
        # VectorRecord's metadata dict.
        for r in vector_store.records:
            assert r.metadata.get("title")
            assert r.metadata.get("summary")
            assert isinstance(r.metadata.get("tags"), list)
            assert r.metadata.get("refined_by") in {
                "rule", "llm", "rule", "error", "skipped"
            }


# ---------------------------------------------------------------------------
# Error handling — each stage's failure produces a clear PipelineStageError
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_missing_file_raises_at_integrity_stage(self, tmp_path):
        pipeline, *_ = _build_pipeline(tmp_path)
        with pytest.raises(PipelineStageError) as exc:
            pipeline.run(str(tmp_path / "nope.pdf"))
        assert exc.value.stage == "integrity"
        assert "not found" in str(exc.value).lower()

    def test_non_pdf_file_raises_at_load_stage(self, tmp_path):
        fake = tmp_path / "fake.pdf"
        fake.write_text("not a real pdf", encoding="utf-8")
        pipeline, *_ = _build_pipeline(tmp_path)
        with pytest.raises(PipelineStageError) as exc:
            pipeline.run(str(fake))
        assert exc.value.stage == "load"

    def test_chunk_error_raises_at_chunk_stage(self, tmp_path, sample_pdf):
        class BrokenChunker(DocumentChunker):
            def split_document(self, document):
                raise RuntimeError("chunking is on fire")

        pipeline, *_ = _build_pipeline(tmp_path)
        pipeline.chunker = BrokenChunker(pipeline.chunker.splitter)
        with pytest.raises(PipelineStageError) as exc:
            pipeline.run(str(sample_pdf))
        assert exc.value.stage == "chunk"
        assert "chunking is on fire" in str(exc.value)

    def test_transform_error_raises_at_transform_stage(
        self, tmp_path, sample_pdf
    ):
        from src.ingestion.transform import BaseTransform

        class BrokenTransform(BaseTransform):
            name = "broken"
            def transform(self, chunks, trace=None):
                raise RuntimeError("transform explosion")

        pipeline, *_ = _build_pipeline(tmp_path)
        pipeline.transforms = [BrokenTransform()]
        with pytest.raises(PipelineStageError) as exc:
            pipeline.run(str(sample_pdf))
        assert exc.value.stage.startswith("transform:")
        assert "transform explosion" in str(exc.value)

    def test_encode_error_raises_at_encode_stage(
        self, tmp_path, sample_pdf
    ):
        pipeline, *_ = _build_pipeline(tmp_path)
        # Force the batch processor to fail by swapping in a broken
        # dense encoder.
        class BoomEnc(BaseEmbedding):
            @property
            def dimensions(self):
                return 4
            def embed(self, texts, **kwargs):
                raise RuntimeError("encoder down")
        pipeline.batch_processor.dense = _DenseEnc(BoomEnc())

        with pytest.raises(PipelineStageError) as exc:
            pipeline.run(str(sample_pdf))
        assert exc.value.stage == "encode"

    def test_vector_store_error_raises_at_vector_store_stage(
        self, tmp_path, sample_pdf
    ):
        pipeline, vector_store, *_ = _build_pipeline(tmp_path)
        vector_store.upsert = lambda *a, **kw: (_ for _ in ()).throw(
            RuntimeError("store dead")
        )
        with pytest.raises(PipelineStageError) as exc:
            pipeline.run(str(sample_pdf))
        assert exc.value.stage == "vector_store"


# ---------------------------------------------------------------------------
# Configuration knobs
# ---------------------------------------------------------------------------

class TestConfig:
    def test_no_transforms_is_ok(self, sample_pdf, tmp_path):
        """Pipeline without any transforms still produces all artifacts."""
        pipeline, vector_store, bm25, image_storage, _, _ = (
            _build_pipeline(tmp_path, include_transforms=False)
        )
        result = pipeline.run(str(sample_pdf))
        assert result.n_chunks > 0
        assert vector_store.count() == result.n_chunks
        assert result.n_images_saved == 1

    def test_no_image_storage_still_works(
        self, sample_pdf, tmp_path
    ):
        """If image_storage is None, image extraction in the
        loader still happens (PdfLoader writes to disk directly) —
        the pipeline just doesn't index them in SQLite."""
        pipeline, vector_store, _, _, _, _ = (
            _build_pipeline(tmp_path, include_image_storage=False)
        )
        result = pipeline.run(str(sample_pdf))
        assert result.n_chunks > 0
        assert vector_store.count() == result.n_chunks
        # Loader still wrote the image file even though we didn't
        # register it in ImageStorage.
        assert result.n_images_saved == 0
        img_files = list((tmp_path / "images").rglob("*.png"))
        assert img_files  # at least one image file on disk

    def test_bm25_index_name_respected(self, sample_pdf, tmp_path):
        pipeline, *_ = _build_pipeline(tmp_path)
        pipeline.bm25_index_name = "custom_name"
        pipeline.run(str(sample_pdf))
        assert (tmp_path / "bm25" / "custom_name.json").exists()


# ---------------------------------------------------------------------------
# Trace (F4)
# ---------------------------------------------------------------------------

class TestTrace:
    """
    F4: orchestrator-level trace events for the five ingestion stages.

    A single IngestionPipeline.run() should produce, in order:
    load → split → transform (one event per transform) → embed → upsert.

    Each non-skipped stage event carries ``method`` and
    ``elapsed_ms``; the ``trace_type`` is ``"ingestion"``.
    """

    EXPECTED_STAGES = [
        "load",
        "split",
        "transform",   # one event per configured transform
        "embed",
        "upsert",
    ]

    def test_ingestion_trace_records_all_five_stages(
        self, sample_pdf, tmp_path
    ):
        from src.core.trace import new_trace
        pipeline, *_ = _build_pipeline(tmp_path)
        trace = new_trace(trace_type="ingestion")
        pipeline.run(str(sample_pdf), trace=trace)
        names = [s["name"] for s in trace.stages]
        for stage in self.EXPECTED_STAGES:
            assert stage in names, (
                f"missing stage {stage!r} in trace: {names}"
            )

    def test_each_stage_has_method_and_elapsed_ms(
        self, sample_pdf, tmp_path
    ):
        from src.core.trace import new_trace
        pipeline, *_ = _build_pipeline(tmp_path)
        trace = new_trace(trace_type="ingestion")
        pipeline.run(str(sample_pdf), trace=trace)
        # Every "stage" event carries method + elapsed_ms. Special
        # events (skipped / error / empty_chunks) are excluded
        # because they don't represent work that ran.
        for s in trace.stages:
            if s["name"] not in self.EXPECTED_STAGES:
                continue
            assert "method" in s, f"stage {s['name']!r} missing 'method'"
            assert "elapsed_ms" in s, (
                f"stage {s['name']!r} missing 'elapsed_ms'"
            )
            assert isinstance(s["method"], str)
            assert s["method"], f"empty method on stage {s['name']!r}"
            assert s["elapsed_ms"] >= 0.0

    def test_transform_stage_records_one_event_per_transform(
        self, sample_pdf, tmp_path
    ):
        """The default pipeline has two transforms
        (ChunkRefiner + MetadataEnricher). Each must produce
        a separate ``transform`` event carrying its own
        ``method`` (= the transform's ``name``)."""
        from src.core.trace import new_trace
        pipeline, *_ = _build_pipeline(tmp_path)
        trace = new_trace(trace_type="ingestion")
        pipeline.run(str(sample_pdf), trace=trace)
        transform_events = [
            s for s in trace.stages if s["name"] == "transform"
        ]
        # One per configured transform (build_pipeline wires up
        # ChunkRefiner + MetadataEnricher by default).
        assert len(transform_events) == 2
        methods = {e["method"] for e in transform_events}
        # Transform names from src.ingestion.transform.* classes.
        assert "chunk_refiner" in methods
        assert "metadata_enricher" in methods

    def test_trace_to_dict_has_ingestion_type(
        self, sample_pdf, tmp_path
    ):
        from src.core.trace import new_trace
        pipeline, *_ = _build_pipeline(tmp_path)
        trace = new_trace(trace_type="ingestion")
        pipeline.run(str(sample_pdf), trace=trace)
        d = trace.to_dict()
        assert d["trace_type"] == "ingestion"
        # search()/run() don't call finish() themselves — the
        # caller (TraceCollector) is responsible for that.
        assert d["finished_at"] is None

    def test_default_trace_type_is_query_or_ingestion(self):
        """Smoke-check the constructor: callers must opt in to
        ``"ingestion"`` rather than rely on a side default that
        could shadow query traces."""
        from src.core.trace import new_trace
        assert new_trace().trace_type == "ingestion"
        assert new_trace(trace_type="query").trace_type == "query"

    def test_trace_records_skip_event(self, sample_pdf, tmp_path):
        """A second run of the same file is a no-op; the trace
        records exactly one ``event="skipped"`` record on the
        pipeline-level stage and no per-stage work events."""
        from src.core.trace import new_trace
        pipeline, *_ = _build_pipeline(tmp_path)
        # First run: real ingestion.
        first = new_trace(trace_type="ingestion")
        pipeline.run(str(sample_pdf), trace=first)
        first_names = [s["name"] for s in first.stages]
        assert "load" in first_names  # sanity: first run worked

        # Second run: skipped.
        second = new_trace(trace_type="ingestion")
        pipeline.run(str(sample_pdf), trace=second)
        # No real work happened — no load/split/embed/upsert.
        for stage in ("load", "split", "embed", "upsert"):
            assert stage not in [s["name"] for s in second.stages]
        # A skipped marker is present on the pipeline-level stage.
        skipped = [
            s for s in second.stages
            if s.get("event") == "skipped"
        ]
        assert len(skipped) == 1
        assert skipped[0]["name"] == "ingestion_pipeline"

    def test_no_trace_argument_does_not_raise(
        self, sample_pdf, tmp_path
    ):
        """trace=None must be a clean no-op."""
        pipeline, *_ = _build_pipeline(tmp_path)
        result = pipeline.run(str(sample_pdf))  # no trace
        assert isinstance(result, PipelineResult)
        assert not result.skipped
        assert result.n_chunks > 0

    def test_trace_records_load_error_event(
        self, tmp_path
    ):
        """A failure at the load stage records an
        ``event="error"`` on the load stage before re-raising."""
        from src.core.trace import new_trace
        fake = tmp_path / "fake.pdf"
        fake.write_text("not a real pdf", encoding="utf-8")
        pipeline, *_ = _build_pipeline(tmp_path)
        trace = new_trace(trace_type="ingestion")
        with pytest.raises(PipelineStageError):
            pipeline.run(str(fake), trace=trace)
        load_errors = [
            s for s in trace.stages
            if s.get("name") == "load" and s.get("event") == "error"
        ]
        assert len(load_errors) == 1
        assert "method" in load_errors[0]
        assert "error" in load_errors[0]

    def test_trace_records_transform_error_event(
        self, sample_pdf, tmp_path
    ):
        """A failing transform records an ``event="error"`` on
        the ``transform`` stage with the transform's ``name``
        as the ``method``."""
        from src.core.trace import new_trace
        from src.ingestion.transform import BaseTransform

        class BrokenTransform(BaseTransform):
            name = "broken"
            def transform(self, chunks, trace=None):
                raise RuntimeError("transform explosion")

        pipeline, *_ = _build_pipeline(tmp_path)
        pipeline.transforms = [BrokenTransform()]
        trace = new_trace(trace_type="ingestion")
        with pytest.raises(PipelineStageError):
            pipeline.run(str(sample_pdf), trace=trace)
        errors = [
            s for s in trace.stages
            if s.get("name") == "transform"
            and s.get("event") == "error"
        ]
        assert len(errors) == 1
        assert errors[0]["method"] == "broken"
        assert "explosion" in errors[0]["error"]
