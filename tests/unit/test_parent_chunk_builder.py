from __future__ import annotations

from src.application.contracts import content_version
from src.core.types import Chunk
from src.ingestion.chunking import ParentChunkBuilder


def _child(index, text, **metadata):
    base = {
        "index_format_version": 2, "chunk_level": "child",
        "chunk_index": index, "document_version": content_version("d", "body"),
        "chunk_version": content_version("d", str(index), text),
        "source_span": {"start": index * 10, "end": index * 10 + len(text)},
    }
    base.update(metadata)
    return Chunk(
        id=f"c{index}", text=text, metadata=base,
        start_offset=index * 10, end_offset=index * 10 + len(text),
        source_ref="doc",
    )


def test_markdown_groups_by_heading_and_links_children_deterministically():
    children = [
        _child(0, "a", doc_type="markdown", heading_path=["A"]),
        _child(1, "b", doc_type="markdown", heading_path=["A"]),
        _child(2, "c", doc_type="markdown", heading_path=["B"]),
    ]
    first = ParentChunkBuilder().build(children)
    second = ParentChunkBuilder().build(children)
    assert [p.id for p in first.parents] == [p.id for p in second.parents]
    assert [p.text for p in first.parents] == ["a\n\nb", "c"]
    assert first.parents[0].metadata["retrievable"] is False
    assert first.parents[0].metadata["child_chunk_ids"] == ["c0", "c1"]
    assert [c.metadata["parent_chunk_id"] for c in first.children] == [
        first.parents[0].id, first.parents[0].id, first.parents[1].id,
    ]


def test_pdf_page_groups_table_atomicity_and_window_budget():
    pdf = ParentChunkBuilder().build([
        _child(0, "p1", doc_type="pdf", page_num=1),
        _child(1, "p2", doc_type="pdf", page_num=2),
        _child(2, "p3", doc_type="pdf", page_num=3),
    ])
    assert [p.text for p in pdf.parents] == ["p1\n\np2", "p3"]

    tables = ParentChunkBuilder().build([
        _child(0, "t1", content_type="table", table_index=0),
        _child(1, "t2", content_type="table", table_index=0),
        _child(2, "t3", content_type="table", table_index=1),
    ])
    assert [p.text for p in tables.parents] == ["t1\n\nt2", "t3"]

    windows = ParentChunkBuilder(max_parent_chars=4).build([
        _child(0, "abc"), _child(1, "def"),
    ])
    assert len(windows.parents) == 2


def test_parent_rolls_up_span_assets_and_never_nests():
    result = ParentChunkBuilder().build([
        _child(0, "a", asset_ids=["x"]),
        _child(1, "b", asset_ids=["x", "y"]),
    ])
    parent = result.parents[0]
    assert parent.metadata["source_span"] == {"start": 0, "end": 11}
    assert parent.metadata["asset_ids"] == ["x", "y"]
    assert parent.metadata["parent_chunk_id"] is None


def test_legacy_children_are_upgraded_without_startup_migration():
    legacy = Chunk(id="old", text="body", metadata={"chunk_index": 0},
                   start_offset=2, end_offset=6, source_ref="doc")
    result = ParentChunkBuilder().build([legacy])
    metadata = result.children[0].metadata
    assert metadata["index_format_version"] == 2
    assert metadata["document_version"] == result.parents[0].metadata["document_version"]
    assert metadata["source_span"] == {"start": 2, "end": 6}
