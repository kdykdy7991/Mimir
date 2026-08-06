"""
Unit tests for the M5 Loader Factory + Registry.

``LoaderFactory`` resolves a loader class by file extension (the
``SplitterFactory`` pattern); ``LoaderRegistry`` holds one loader per
extension and dispatches ``load()`` so a single pipeline slot can ingest
mixed-format batches.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.libs.loader import (
    BaseLoader,
    LoaderFactory,
    LoaderRegistry,
    MarkdownLoader,
    PdfLoader,
    UnsupportedFormatError,
)


class _StubLoader(BaseLoader):
    def load(self, path: str):
        raise NotImplementedError  # pragma: no cover


# ---------------------------------------------------------------------------
# LoaderFactory — class registry
# ---------------------------------------------------------------------------

class TestLoaderFactory:
    def test_create_for_ext_returns_known_loaders(self) -> None:
        assert isinstance(LoaderFactory.create_for_ext(".pdf"), PdfLoader)
        assert isinstance(LoaderFactory.create_for_ext(".md"), MarkdownLoader)
        assert isinstance(LoaderFactory.create_for_ext(".markdown"), MarkdownLoader)

    def test_create_for_ext_accepts_without_dot(self) -> None:
        assert isinstance(LoaderFactory.create_for_ext("pdf"), PdfLoader)
        assert isinstance(LoaderFactory.create_for_ext("MD"), MarkdownLoader)

    def test_create_for_path_uses_suffix(self, tmp_path) -> None:
        p = tmp_path / "doc.md"
        p.write_text("# hi", encoding="utf-8")
        assert isinstance(LoaderFactory.create_for_path(str(p)), MarkdownLoader)

    def test_unknown_extension_raises(self) -> None:
        with pytest.raises(UnsupportedFormatError, match="txt"):
            LoaderFactory.create_for_ext(".txt")

    def test_supported_extensions(self) -> None:
        assert {".pdf", ".md", ".markdown"} <= set(LoaderFactory.supported_extensions())

    def test_register_provider_adds_new_extension(self) -> None:
        # Use a real src-package class (not one defined in this test
        # module): ``_import_provider`` round-trips through importlib,
        # which pytest's import machinery can duplicate for test files.
        try:
            LoaderFactory.register_provider(
                ".foo", "src.libs.loader.markdown_loader.MarkdownLoader",
            )
            assert ".foo" in LoaderFactory.supported_extensions()
            assert isinstance(LoaderFactory.create_for_ext(".foo"), MarkdownLoader)
        finally:
            import src.libs.loader.loader_factory as lf
            lf._PROVIDER_REGISTRY.pop(".foo", None)

    def test_create_for_path_unknown_raises(self, tmp_path) -> None:
        p = tmp_path / "doc.bin"
        p.write_bytes(b"\x00")
        with pytest.raises(UnsupportedFormatError):
            LoaderFactory.create_for_path(str(p))


# ---------------------------------------------------------------------------
# LoaderRegistry — instance dispatch
# ---------------------------------------------------------------------------

class TestLoaderRegistry:
    def test_from_settings_builds_default_set(self, tmp_path) -> None:
        reg = LoaderRegistry.from_settings(image_dir=str(tmp_path / "img"))
        assert {".pdf", ".md", ".markdown"} <= set(reg.supported_extensions())
        assert isinstance(reg.loader_for("a.pdf"), PdfLoader)
        assert isinstance(reg.loader_for("b.md"), MarkdownLoader)
        assert isinstance(reg.loader_for("c.MARKDOWN"), MarkdownLoader)

    def test_register_and_is_supported(self) -> None:
        reg = LoaderRegistry()
        assert not reg.is_supported("a.pdf")
        reg.register("pdf", _StubLoader())
        assert reg.is_supported("a.pdf")
        # Case-insensitive on both register and dispatch.
        assert reg.is_supported("a.PDF")
        assert isinstance(reg.loader_for("a.PDF"), _StubLoader)

    def test_load_dispatches_to_registered_loader(self, tmp_path) -> None:
        reg = LoaderRegistry.from_settings(image_dir=str(tmp_path / "img"))
        p = tmp_path / "doc.md"
        p.write_text("# Title\n\nbody\n", encoding="utf-8")
        doc = reg.load(str(p))
        assert doc.metadata["doc_type"] == "markdown"
        assert doc.metadata["title"] == "Title"

    def test_unsupported_extension_raises_on_load(self, tmp_path) -> None:
        reg = LoaderRegistry()
        p = tmp_path / "doc.bin"
        p.write_bytes(b"\x00")
        with pytest.raises(UnsupportedFormatError):
            reg.load(str(p))

    def test_missing_file_raises_before_dispatch(self, tmp_path) -> None:
        reg = LoaderRegistry.from_settings(image_dir=str(tmp_path / "img"))
        with pytest.raises(Exception) as exc_info:
            reg.load(str(tmp_path / "nope.md"))
        from src.libs.loader import LoaderError
        assert isinstance(exc_info.value, LoaderError)
