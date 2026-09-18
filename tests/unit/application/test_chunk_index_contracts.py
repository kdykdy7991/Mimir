from __future__ import annotations

import pytest
import hashlib

from src.application.contracts import (
    AssetV1,
    ChunkIndexMetadata,
    ChunkLevel,
    ContractError,
    SourceSpan,
    content_version,
    stable_asset_id,
    stable_parent_chunk_id,
)


def test_legacy_metadata_reads_fail_soft_with_canonical_aliases():
    metadata = ChunkIndexMetadata.from_mapping({
        "parent_id": "p1", "section": "Intro", "image_ids": ["a1"],
    })
    assert metadata.index_format_version == 1
    assert metadata.chunk_level is ChunkLevel.CHILD
    assert metadata.parent_chunk_id == "p1"
    assert metadata.heading_path == ("Intro",)
    assert metadata.asset_ids == ("a1",)
    assert metadata.document_version is None


def test_v2_metadata_parses_source_span_and_rejects_future_version():
    metadata = ChunkIndexMetadata.from_mapping({
        "index_format_version": 2, "chunk_level": "parent",
        "source_span": {"start": 2, "end": 8},
        "document_version": "dv", "chunk_version": "cv",
    })
    assert metadata.source_span == SourceSpan(2, 8)
    with pytest.raises(ContractError, match="unsupported"):
        ChunkIndexMetadata.from_mapping({"index_format_version": 3})


def test_version_parent_and_asset_ids_are_deterministic_and_scoped():
    version = content_version("doc", "body")
    assert version == content_version("doc", "body")
    assert stable_parent_chunk_id("doc", version, 0) == stable_parent_chunk_id(
        "doc", version, 0,
    )
    assert stable_parent_chunk_id("doc", version, 0) != stable_parent_chunk_id(
        "doc", version, 1,
    )
    assert stable_asset_id("doc", version, "a" * 64).startswith("asset:")


def test_asset_contract_rejects_absolute_and_traversal_locators():
    base = dict(
        asset_id="a", document_id="d", chunk_id="c", document_version="v",
        mime_type="image/png", byte_size=2, checksum_sha256="a" * 64,
    )
    asset = AssetV1(**base, locator="images/a.png")
    assert asset.byte_size == 2
    with pytest.raises(ContractError, match="relative"):
        AssetV1(**base, locator="/secret/a.png")
    with pytest.raises(ContractError, match="traversal"):
        AssetV1(**base, locator="images/../secret")


def test_asset_content_verifies_size_and_checksum():
    from src.application.contracts import AssetContent

    data = b"ok"
    metadata = AssetV1(
        asset_id="a", document_id="d", chunk_id="c", document_version="v",
        mime_type="image/png", byte_size=len(data),
        checksum_sha256=hashlib.sha256(data).hexdigest(),
        locator="images/a.png",
    )
    assert AssetContent(metadata=metadata, data=data).data == data
    with pytest.raises(ContractError, match="checksum"):
        AssetContent(
            metadata=AssetV1(
                **{**metadata.__dict__, "checksum_sha256": "a" * 64},
            ),
            data=data,
        )


def test_source_span_rejects_invalid_bounds():
    with pytest.raises(ContractError):
        SourceSpan(4, 3)
