"""Unit tests for the unified document-parser contract (plan §5).

Covers ``ParseRequest`` / ``ParsedDocument`` / ``ParsedImage`` value semantics
and the stable error taxonomy in ``src.document_parser``.
"""

from __future__ import annotations

import pytest

from src.document_parser import (
    EngineUnavailableError,
    ImagePersistenceError,
    ParseError,
    ParseFailedError,
    ParseRequest,
    ParseStatus,
    ParseTimeoutError,
    ParsedDocument,
    ParsedImage,
    UnsupportedFormatError,
)


class TestParseRequest:
    def test_basic_fields(self) -> None:
        req = ParseRequest(
            source_path="/uploads/plan.pdf",
            file_name="plan.pdf",
            file_type="pdf",
            content=b"%PDF",
            parser_engine="builtin",
            request_id="req-abc",
        )
        assert req.source_path == "/uploads/plan.pdf"
        assert req.file_name == "plan.pdf"
        assert req.file_type == "pdf"
        assert req.content == b"%PDF"
        assert req.request_id == "req-abc"
        assert req.engine_overrides == {}

    def test_content_accepts_bytearray(self) -> None:
        req = ParseRequest(
            source_path="x", file_name="x", file_type="txt",
            content=bytearray(b"hello"),
        )
        assert isinstance(req.content, bytes)
        assert req.content == b"hello"

    def test_content_must_be_bytes(self) -> None:
        with pytest.raises(TypeError):
            ParseRequest(
                source_path="x", file_name="x", file_type="txt",
                content="not bytes",  # type: ignore[arg-type]
            )

    def test_engine_overrides_is_owned_copy(self) -> None:
        overrides = {"max_pages": "2"}
        req = ParseRequest(
            source_path="x", file_name="x", file_type="docx",
            content=b"", engine_overrides=overrides,
        )
        assert req.engine_overrides == {"max_pages": "2"}
        overrides["dpi"] = "200"  # mutate the caller's dict
        assert req.engine_overrides == {"max_pages": "2"}  # frozen/unchanged


class TestParsedImage:
    def test_fields_and_defaults(self) -> None:
        img = ParsedImage(
            filename="page1.png",
            original_ref="fig:1",
            mime_type="image/png",
            data=b"\x89PNG",
            page=3,
            is_original=False,
        )
        assert img.page == 3
        assert not img.is_original

    def test_defaults_page_none_original_false(self) -> None:
        img = ParsedImage(
            filename="a.bin", original_ref="ref", mime_type="image/png", data=b"x",
        )
        assert img.page is None
        assert img.is_original is False


class TestParsedDocument:
    def test_defaults(self) -> None:
        doc = ParsedDocument(markdown="# hi")
        assert doc.markdown == "# hi"
        assert doc.images == []
        assert doc.metadata == {}
        assert doc.warnings == []
        assert doc.parse_status is ParseStatus.SUCCESS

    def test_containers_are_owned_copies(self) -> None:
        images = []
        warnings = ["w"]
        meta = {"k": "v"}
        doc = ParsedDocument(
            markdown="m", images=images, warnings=warnings, metadata=meta,
        )
        images.append(ParsedImage("x", "r", "image/png", b"1"))
        warnings.append("w2")
        meta["k2"] = "v2"
        assert doc.images == []
        assert doc.warnings == ["w"]
        assert doc.metadata == {"k": "v"}

    def test_parse_status_override(self) -> None:
        doc = ParsedDocument(markdown="m", parse_status=ParseStatus.PARTIAL_SUCCESS)
        assert doc.parse_status.value == "partial_success"


class TestParseErrors:
    def test_base_and_typed_hierarchy(self) -> None:
        for exc in (
            UnsupportedFormatError("x"),
            EngineUnavailableError("x"),
            ParseTimeoutError("x"),
            ParseFailedError("x"),
            ImagePersistenceError("x"),
        ):
            assert isinstance(exc, ParseError)

    def test_parse_failed_error_carries_attempts(self) -> None:
        exc = ParseFailedError(
            "boom", attempts=[{"engine": "builtin", "ok": False}],
        )
        assert exc.attempts == [{"engine": "builtin", "ok": False}]
        assert "" in str(exc)