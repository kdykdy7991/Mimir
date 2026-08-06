# Loader

from src.libs.loader.base_loader import BaseLoader, LoaderError
from src.libs.loader.file_integrity import (
    DEFAULT_DB_PATH,
    FileIntegrityChecker,
    FileIntegrityError,
    IngestionRecord,
    MissingFileError,
    SQLiteIntegrityChecker,
)
from src.libs.loader.loader_factory import (
    LoaderFactory,
    LoaderRegistry,
    UnsupportedFormatError,
)
from src.libs.loader.markdown_loader import (
    DEFAULT_ENCODING,
    MarkdownLoader,
)
from src.libs.loader.pdf_loader import (
    DEFAULT_IMAGE_DIR,
    PdfLoader,
    extract_image_mentions,
)

__all__ = [
    # C3 — Loader abstraction
    "BaseLoader",
    "LoaderError",
    "DEFAULT_IMAGE_DIR",
    "PdfLoader",
    "extract_image_mentions",
    # M5 — Markdown + unified factory/registry
    "DEFAULT_ENCODING",
    "MarkdownLoader",
    "LoaderFactory",
    "LoaderRegistry",
    "UnsupportedFormatError",
    # C2 — File integrity
    "DEFAULT_DB_PATH",
    "FileIntegrityChecker",
    "FileIntegrityError",
    "IngestionRecord",
    "MissingFileError",
    "SQLiteIntegrityChecker",
]
