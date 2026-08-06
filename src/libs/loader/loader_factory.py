"""
Loader factory + registry — selects a ``BaseLoader`` by file type.

Two layers, mirroring the splitter/embedding factory pattern:

- :class:`LoaderFactory` — a class-path registry mapping file extensions
  to loader classes (``".pdf" -> PdfLoader``, ``".md"/".markdown" ->
  MarkdownLoader``). ``create_for_path`` instantiates the right loader
  for a file. New formats register via ``register_provider`` without
  touching call sites.
- :class:`LoaderRegistry` — an *instance* registry holding one ready
  loader per extension. It is itself a ``BaseLoader`` whose ``load()``
  dispatches on the file's extension, so the ``IngestionPipeline`` keeps
  its single ``loader`` slot while ingesting mixed-format batches
  (PDF + Markdown in one run). ``from_settings`` builds the default
  set (PDF + Markdown) with the image configuration ``PdfLoader`` needs.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Mapping

from src.libs.loader.base_loader import BaseLoader, LoaderError

# Extension (lowercased) -> loader class dotted path. Mirror of
# ``SplitterFactory._PROVIDER_REGISTRY``.
_PROVIDER_REGISTRY: dict[str, str] = {
    ".pdf": "src.libs.loader.pdf_loader.PdfLoader",
    ".md": "src.libs.loader.markdown_loader.MarkdownLoader",
    ".markdown": "src.libs.loader.markdown_loader.MarkdownLoader",
}


class UnsupportedFormatError(LoaderError):
    """Raised when no loader is registered for a file's extension."""


def _import_provider(provider_path: str) -> type[BaseLoader]:
    """Dynamically import a loader class from a dotted path."""
    module_path, class_name = provider_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


class LoaderFactory:
    """Create ``BaseLoader`` instances by file extension."""

    @staticmethod
    def create_for_ext(ext: str, **kwargs: Any) -> BaseLoader:
        """Instantiate the loader registered for ``ext`` (``".pdf"`` / ``"pdf"``).

        Raises ``UnsupportedFormatError`` for unknown extensions.
        ``kwargs`` are forwarded to the loader constructor, so image
        loaders (e.g. ``PdfLoader(image_dir=..., image_classifier=...)``)
        can be configured while text-only loaders stay simple.
        """
        key = ext.lower() if ext.startswith(".") else f".{ext.lower()}"
        if key not in _PROVIDER_REGISTRY:
            supported = ", ".join(sorted(_PROVIDER_REGISTRY))
            raise UnsupportedFormatError(
                f"Unsupported file type {ext!r}. "
                f"Supported extensions: {supported}",
            )
        try:
            provider_class = _import_provider(_PROVIDER_REGISTRY[key])
            return provider_class(**kwargs)
        except ImportError as exc:
            raise LoaderError(
                f"Failed to import loader for {key}: {exc}. "
                f"Make sure the required dependency is installed.",
            ) from exc

    @staticmethod
    def create_for_path(path: str, **kwargs: Any) -> BaseLoader:
        """Instantiate the loader for ``path`` based on its suffix."""
        return LoaderFactory.create_for_ext(Path(path).suffix, **kwargs)

    @staticmethod
    def register_provider(ext: str, class_path: str) -> None:
        """Register (or override) the loader class for ``ext``."""
        key = ext.lower() if ext.startswith(".") else f".{ext.lower()}"
        _PROVIDER_REGISTRY[key] = class_path

    @staticmethod
    def supported_extensions() -> frozenset[str]:
        """Return the set of registered extensions (lowercased, with dot)."""
        return frozenset(_PROVIDER_REGISTRY)

    @staticmethod
    def is_supported(path: str) -> bool:
        """True iff a loader is registered for ``path``'s suffix."""
        return Path(path).suffix.lower() in _PROVIDER_REGISTRY


class LoaderRegistry(BaseLoader):
    """Dispatch ``load()`` to a registered loader based on file extension.

    Behaves as a single ``BaseLoader`` so ``IngestionPipeline`` keeps its
    ``loader`` slot unchanged; ``load(path)`` picks the loader for the
    file's extension and delegates. A failure in one file's loader does
    not affect others (callers use ``load`` per file inside try/except).
    """

    def __init__(
        self,
        loaders: Mapping[str, BaseLoader] | None = None,
    ) -> None:
        self._loaders: dict[str, BaseLoader] = {}
        for ext, loader in (loaders or {}).items():
            self.register(ext, loader)

    def register(self, ext: str, loader: BaseLoader) -> None:
        """Bind ``loader`` to ``ext`` (lowercased; ``".pdf"`` or ``"pdf"``)."""
        key = ext.lower() if ext.startswith(".") else f".{ext.lower()}"
        self._loaders[key] = loader

    def is_supported(self, path: str) -> bool:
        return Path(path).suffix.lower() in self._loaders

    def loader_for(self, path: str) -> BaseLoader:
        """Return the loader bound to ``path``'s extension.

        Raises ``UnsupportedFormatError`` when the extension is not
        registered.
        """
        ext = Path(path).suffix.lower()
        loader = self._loaders.get(ext)
        if loader is None:
            supported = ", ".join(sorted(self._loaders))
            raise UnsupportedFormatError(
                f"Unsupported file type {ext!r}. "
                f"Registered extensions: {supported}",
            )
        return loader

    def load(self, path: str):
        """Load ``path`` with the loader for its extension."""
        self._require_file(path)
        return self.loader_for(path).load(path)

    def supported_extensions(self) -> frozenset[str]:
        return frozenset(self._loaders)

    @classmethod
    def from_settings(
        cls,
        *,
        image_dir: str,
        image_classifier: Any = None,
        markdown_encoding: str = "utf-8-sig",
    ) -> "LoaderRegistry":
        """Build the default registry: PDF + Markdown loaders.

        ``image_dir`` / ``image_classifier`` configure ``PdfLoader``;
        the Markdown loaders only take the optional encoding.
        """
        from src.libs.loader.markdown_loader import MarkdownLoader
        from src.libs.loader.pdf_loader import PdfLoader

        return cls(
            {
                ".pdf": PdfLoader(
                    image_dir=image_dir,
                    image_classifier=image_classifier,
                ),
                ".md": MarkdownLoader(encoding=markdown_encoding),
                ".markdown": MarkdownLoader(encoding=markdown_encoding),
            },
        )


__all__ = [
    "LoaderFactory",
    "LoaderRegistry",
    "UnsupportedFormatError",
]
