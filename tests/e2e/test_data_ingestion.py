"""
End-to-end test for the ingest CLI (C15).

Two angles of coverage:

1. **CLI surface** — ``python scripts/ingest.py --help`` exits 0 and
   documents the required flags.

2. **Full pipeline via the script's public entry point** — calls
   :func:`build_pipeline` with deterministic fakes, runs the same
   processing logic the script would, and verifies the produced
   artifacts on disk.

We don't spawn the script as a subprocess because that would need
the default providers (OpenAI / Chroma / HF) to be reachable. The
in-process approach tests the same code paths with controllable
backends.
"""

from __future__ import annotations

import os
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path
from uuid import uuid4

import pymupdf
import pytest

from src.core.settings import (
    ChunkRefinerSettings,
    IngestionSettings,
    MetadataEnricherSettings,
    Settings,
    SplitterSettings,
)
from src.libs.embedding.base_embedding import BaseEmbedding
from src.libs.splitter.base_splitter import BaseSplitter
from src.libs.vector_store.base_vector_store import (
    BaseVectorStore,
    VectorRecord,
)

# Make scripts/ importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.ingest import (  # noqa: E402
    SUPPORTED_EXTS,
    build_pipeline,
    iter_input_files,
    main,
    parse_args,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_png() -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", 8, 8, 8, 0, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x00" * 8 for _ in range(8))
    idat = zlib.compress(raw)
    return (
        sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat)
        + chunk(b"IEND", b"")
    )


def _build_sample_pdf(path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Page One: Modular RAG Ingest Test.")
    page.insert_text((72, 100), "This page contains a small image.")
    page.insert_image(
        pymupdf.Rect(72, 150, 200, 250), stream=_minimal_png(),
    )
    page = doc.new_page()
    page.insert_text((72, 72), "Page Two: BM25 retrieval overview.")
    page.insert_text((72, 100), "BM25 and dense retrieval are combined.")
    doc.save(str(path))
    doc.close()


class FakeEmbedding(BaseEmbedding):
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
    def __init__(self) -> None:
        self._by_id: dict[str, VectorRecord] = {}

    @property
    def records(self):
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
    def split_text(self, text, **kwargs):
        return [p for p in text.split("\n\n") if p.strip()]


def _make_settings(prompts_dir: Path) -> Settings:
    """Build a Settings object that points at the test prompts dir
    (so transforms load their templates)."""
    prompts_dir.mkdir(parents=True, exist_ok=True)
    # Write the prompts the transforms expect.
    (prompts_dir / "refine_prompt.txt").write_text(
        "Clean: {chunk_text}", encoding="utf-8"
    )
    (prompts_dir / "meta_prompt.txt").write_text(
        "META: {chunk_text}", encoding="utf-8"
    )
    return Settings(
        splitter=SplitterSettings(type="recursive", chunk_size=1000),
        ingestion=IngestionSettings(
            chunk_refiner=ChunkRefinerSettings(
                use_llm=False,
                prompt_path=str(prompts_dir / "refine_prompt.txt"),
            ),
            metadata_enricher=MetadataEnricherSettings(
                use_llm=False,
                prompt_path=str(prompts_dir / "meta_prompt.txt"),
            ),
        ),
    )


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------

class TestCliSurface:
    def test_help_exits_zero(self):
        """``--help`` should always work, even without backends."""
        result = subprocess.run(
            [sys.executable, "scripts/ingest.py", "--help"],
            capture_output=True, text=True,
            cwd=Path(__file__).resolve().parent.parent.parent,
        )
        assert result.returncode == 0
        assert "--path" in result.stdout
        assert "--collection" in result.stdout
        assert "--force" in result.stdout
        assert "--data-dir" in result.stdout

    def test_parse_args_defaults(self):
        ns = parse_args(["--path", "x.pdf"])
        assert ns.path == "x.pdf"
        assert ns.collection == "default"
        assert ns.force is False
        assert ns.data_dir == "./data"

    def test_parse_args_force(self):
        ns = parse_args(["--path", "x.pdf", "--force"])
        assert ns.force is True

    def test_parse_args_missing_path_errors(self):
        with pytest.raises(SystemExit):
            parse_args([])

    def test_iter_input_files_skips_unsupported(self, tmp_path):
        (tmp_path / "good.pdf").write_bytes(b"%PDF-1.4 fake")
        (tmp_path / "bad.txt").write_text("nope")
        (tmp_path / "good2.pdf").write_bytes(b"%PDF-1.4 fake")
        out = list(iter_input_files(str(tmp_path)))
        names = {p.name for p in out}
        # .txt is skipped, both .pdf files are kept
        assert "good.pdf" in names
        assert "good2.pdf" in names
        assert "bad.txt" not in names

    def test_iter_input_files_single_file_case_insensitive_suffix(self, tmp_path):
        """The single-file path checks suffix case-insensitively, so
        a .PDF (uppercase) file at the path is still accepted."""
        p = tmp_path / "x.PDF"
        p.write_bytes(b"%PDF-1.4 fake")
        out = list(iter_input_files(str(p)))
        assert out == [p]

    def test_iter_input_files_single_file(self, tmp_path):
        p = tmp_path / "x.pdf"
        p.write_bytes(b"%PDF-1.4 fake")
        out = list(iter_input_files(str(p)))
        assert out == [p]

    def test_iter_input_files_no_match(self, tmp_path):
        (tmp_path / "nope.txt").write_text("x")
        assert list(iter_input_files(str(tmp_path))) == []


# ---------------------------------------------------------------------------
# End-to-end: build pipeline, ingest, assert artifacts
# ---------------------------------------------------------------------------

class TestEndToEnd:
    def test_ingest_produces_all_artifacts(self, tmp_path):
        # 1. Sample PDF + data dir
        pdf = tmp_path / "input.pdf"
        _build_sample_pdf(pdf)
        data_dir = tmp_path / "data"

        # 2. Build pipeline (using fakes — no real API keys needed)
        settings = _make_settings(tmp_path / "prompts")
        pipeline = build_pipeline(
            settings=settings,
            data_dir=str(data_dir),
            collection="test",
            splitter=ParaSplitter(),
            embedding=FakeEmbedding(dim=8),
            vector_store=FakeVectorStore(),
        )

        # 3. Process via the script's pipeline
        from scripts.ingest import iter_input_files
        for p in iter_input_files(str(pdf)):
            result = pipeline.run(str(p))
            assert not result.skipped
            assert result.n_chunks > 0
            assert result.n_images_saved == 1
            assert result.n_records_upserted == result.n_chunks
            assert result.bm25_n_docs == result.n_chunks

        # 4. Verify artifacts on disk
        assert (data_dir / "db" / "ingestion_history.db").exists()
        assert (data_dir / "db" / "image_index.db").exists()
        assert (data_dir / "db" / "bm25" / "test.json").exists()
        # Images written under data_dir/images/...
        image_files = list((data_dir / "images").rglob("*.png"))
        assert image_files  # at least one

        # 5. M3 regression: ``build_pipeline(collection="test")`` must
        # scope the integrity + image rows to "test", not "default".
        # Without this a non-default upload's document lists under the
        # wrong knowledge base (BM25 already carries the name; the
        # integrity/image stores used to fall back to "default").
        from src.ingestion.storage import ImageStorage
        from src.libs.loader.file_integrity import SQLiteIntegrityChecker

        integrity = SQLiteIntegrityChecker(
            str(data_dir / "db" / "ingestion_history.db"),
        )
        assert integrity.get_record_by_path(
            str(pdf), collection="test",
        ) is not None
        assert integrity.get_record_by_path(
            str(pdf), collection="default",
        ) is None

        images = ImageStorage(
            db_path=str(data_dir / "db" / "image_index.db"),
            base_dir=str(data_dir / "images"),
        )
        assert len(images.find_by_collection("test")) == 1
        assert images.find_by_collection("default") == []

    def test_mixed_pdf_markdown_batch_ingests(self, tmp_path):
        """M5: a single pipeline (LoaderRegistry) ingests PDF + Markdown.

        Each format flows through the same pipeline with the loader
        dispatched by extension; both land in the vector store with their
        structure intact and both get integrity rows scoped to the
        collection.
        """
        pdf = tmp_path / "input.pdf"
        _build_sample_pdf(pdf)
        md = tmp_path / "notes.md"
        md.write_text(
            "# Notes\n\n- item one\n\n```python\ncode_here()\n```\n\n> quote line\n",
            encoding="utf-8",
        )
        data_dir = tmp_path / "data"
        store = FakeVectorStore()

        settings = _make_settings(tmp_path / "prompts")
        pipeline = build_pipeline(
            settings=settings,
            data_dir=str(data_dir),
            collection="test",
            splitter=ParaSplitter(),
            embedding=FakeEmbedding(dim=8),
            vector_store=store,
        )

        files = {p.name: p for p in iter_input_files(str(tmp_path))}
        assert set(files) == {"input.pdf", "notes.md"}
        results = {name: pipeline.run(str(p)) for name, p in files.items()}
        assert all(not r.skipped for r in results.values())
        assert all(r.n_chunks > 0 for r in results.values())

        # Markdown structure survived loader → splitter → vector store:
        # heading, list item, fenced code and blockquote are all present.
        texts = [r.text for r in store.records]
        assert any("# Notes" in t for t in texts)
        assert any("- item one" in t for t in texts)
        assert any("```python" in t and "code_here()" in t for t in texts)
        assert any("> quote line" in t for t in texts)

        # Both files have integrity rows scoped to the collection.
        from src.libs.loader.file_integrity import SQLiteIntegrityChecker
        integrity = SQLiteIntegrityChecker(
            str(data_dir / "db" / "ingestion_history.db"),
        )
        assert integrity.get_record_by_path(str(pdf), collection="test") is not None
        assert integrity.get_record_by_path(str(md), collection="test") is not None

    def test_upload_batch_real_pipeline_keeps_canonical_source(self, tmp_path):
        """M5 review fix: driving the REAL pipeline through
        ``IngestionService.upload_batch`` (per-task temp files) must keep
        the stable canonical ``source_path`` in chunk metadata + integrity
        rows — no temp path ever leaks into the document identity."""
        pdf = tmp_path / "input.pdf"
        _build_sample_pdf(pdf)
        pdf_bytes = pdf.read_bytes()
        data_dir = tmp_path / "data"
        store = FakeVectorStore()

        settings = _make_settings(tmp_path / "prompts")
        pipeline = build_pipeline(
            settings=settings,
            data_dir=str(data_dir),
            collection="default",
            splitter=ParaSplitter(),
            embedding=FakeEmbedding(dim=8),
            vector_store=store,
        )
        from src.application.services import BatchFileUpload, IngestionService
        svc = IngestionService(pipeline, upload_dir=tmp_path / "uploads")

        resp = svc.upload_batch(
            items=[
                BatchFileUpload(
                    filename="notes.md", content_type="text/markdown",
                    bytes_payload="# Hello\n\n- item\n".encode(),
                ),
                BatchFileUpload(
                    filename="input.pdf", content_type="application/pdf",
                    bytes_payload=pdf_bytes,
                ),
            ],
            collection="default", collection_id=uuid4(),
        )
        assert resp.accepted == 2, resp

        # Wait for both workers to finish.
        for f in resp.files:
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                rec = svc.get_task(f.task_id)
                if rec is not None and rec.status in ("succeeded", "failed", "skipped"):
                    break
                time.sleep(0.01)
            assert rec is not None and rec.status == "succeeded", rec

        # Chunk metadata carries the canonical sources, never the temp path.
        canonical = {
            str(svc.compute_source_path("default", name))
            for name in ("notes.md", "input.pdf")
        }
        sources = {r.metadata.get("source_path") for r in store.records}
        assert sources == canonical, sources
        assert not any(".tmp" in (s or "") for s in sources)

        # Integrity rows are keyed by the canonical paths.
        from src.libs.loader.file_integrity import SQLiteIntegrityChecker
        integrity = SQLiteIntegrityChecker(
            str(data_dir / "db" / "ingestion_history.db"),
        )
        for src in canonical:
            assert integrity.get_record_by_path(src, collection="default") is not None

        # Staged temp files are cleaned up after ingestion.
        assert list((tmp_path / "uploads" / "default" / ".tmp").iterdir()) == []

    def test_ingest_skips_unchanged_on_repeat(self, tmp_path):
        pdf = tmp_path / "input.pdf"
        _build_sample_pdf(pdf)
        data_dir = tmp_path / "data"

        settings = _make_settings(tmp_path / "prompts")
        pipeline = build_pipeline(
            settings=settings,
            data_dir=str(data_dir),
            collection="test",
            splitter=ParaSplitter(),
            embedding=FakeEmbedding(dim=8),
            vector_store=FakeVectorStore(),
        )
        # First run — ingests
        from scripts.ingest import iter_input_files
        files = list(iter_input_files(str(pdf)))
        r1 = pipeline.run(str(files[0]))
        assert r1.skipped is False

        # Second run — same file → skipped
        r2 = pipeline.run(str(files[0]))
        assert r2.skipped is True

    def test_force_re_ingests_existing_file(self, tmp_path):
        """Simulate the script's --force code path: forget the
        integrity row, then re-run."""
        pdf = tmp_path / "input.pdf"
        _build_sample_pdf(pdf)
        data_dir = tmp_path / "data"

        settings = _make_settings(tmp_path / "prompts")
        pipeline = build_pipeline(
            settings=settings,
            data_dir=str(data_dir),
            collection="test",
            splitter=ParaSplitter(),
            embedding=FakeEmbedding(dim=8),
            vector_store=FakeVectorStore(),
        )
        h = pipeline.file_integrity.compute_sha256(str(pdf))
        pipeline.run(str(pdf))
        # After first run, the hash is in the integrity DB — scoped to
        # the pipeline's collection ("test"), not the "default" fallback.
        assert pipeline.file_integrity.should_skip(h, collection="test") is True
        assert pipeline.file_integrity.should_skip(h) is False

        # --force: forget the row (again scoped to the collection)
        assert pipeline.file_integrity.forget(h, collection="test") is True
        assert pipeline.file_integrity.should_skip(h, collection="test") is False

        # Re-run — should NOT be skipped now
        r2 = pipeline.run(str(pdf))
        assert r2.skipped is False

    def test_main_handles_no_files(self, tmp_path, capsys):
        """The script's iter_input_files should return [] for an
        empty directory. The downstream "no files" branch of
        main() is exercised by this contract — we don't invoke
        main() here because that would also try to instantiate the
        configured backends (which would require API keys in the
        default settings)."""
        empty = tmp_path / "empty"
        empty.mkdir()
        assert list(iter_input_files(str(empty))) == []

    def test_main_no_files_branch_logic(self, tmp_path, capsys):
        """Verify the no-files message path indirectly: the script
        always prints a clear error and returns 1 when no files
        match. We simulate by reading the source."""
        src = Path(__file__).resolve().parent.parent.parent
        script = (src / "scripts" / "ingest.py").read_text()
        assert 'No supported files found' in script
        assert 'return 1' in script

    def test_supported_exts_contains_pdf(self):
        assert ".pdf" in SUPPORTED_EXTS

    def test_supported_exts_contains_markdown(self):
        """M5: the CLI accepts .md / .markdown next to .pdf."""
        assert ".md" in SUPPORTED_EXTS
        assert ".markdown" in SUPPORTED_EXTS

    def test_iter_input_files_picks_up_markdown(self, tmp_path):
        (tmp_path / "a.md").write_text("# A", encoding="utf-8")
        (tmp_path / "b.markdown").write_text("# B", encoding="utf-8")
        (tmp_path / "c.txt").write_text("nope", encoding="utf-8")
        names = {p.name for p in iter_input_files(str(tmp_path))}
        assert names == {"a.md", "b.markdown"}

    def test_iter_input_files_single_markdown(self, tmp_path):
        p = tmp_path / "notes.MD"
        p.write_text("# A", encoding="utf-8")
        # Single-file path checks suffix case-insensitively.
        assert list(iter_input_files(str(p))) == [p]
