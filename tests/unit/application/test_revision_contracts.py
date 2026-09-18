from datetime import datetime, timezone

import pytest

from src.application.contracts import ChunkRevision, ContractError, RevisionSource


def test_revision_checksum_is_derived_and_content_is_immutable():
    revision = ChunkRevision(
        revision_id="r1", collection="kb", document_id="d", chunk_id="c",
        text="body", metadata={"title": "T"}, reason="fix", actor="user:1",
        created_at=datetime.now(timezone.utc),
    )
    assert len(revision.checksum_sha256) == 64
    with pytest.raises(ContractError, match="checksum"):
        ChunkRevision(
            revision_id="r2", collection="kb", document_id="d", chunk_id="c",
            text="body", reason="fix", actor="user:1", checksum_sha256="a" * 64,
        )


def test_initial_and_rollback_invariants():
    with pytest.raises(ContractError, match="base"):
        ChunkRevision(
            revision_id="r", collection="kb", document_id="d", chunk_id="c",
            text="x", source=RevisionSource.INITIAL, base_revision_id="old",
            reason="initial", actor="system",
        )
    with pytest.raises(ContractError, match="target"):
        ChunkRevision(
            revision_id="r", collection="kb", document_id="d", chunk_id="c",
            text="x", source=RevisionSource.ROLLBACK, base_revision_id="old",
            reason="rollback", actor="user",
        )
