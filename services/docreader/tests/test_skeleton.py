"""Phase-0 skeleton checks: the package is importable, versioned, and the
service entrypoint is not (yet) callable — i.e. nothing pretends to serve.
"""

from __future__ import annotations

import pytest

from docreader import __version__
from docreader import main


def test_package_version_present() -> None:
    assert isinstance(__version__, str)
    assert __version__


def test_config_module_importable() -> None:
    import docreader.config  # noqa: F401
    import docreader.parser  # noqa: F401
    import docreader.models  # noqa: F401
    import docreader.utils  # noqa: F401


def test_serve_not_implemented_yet() -> None:
    with pytest.raises(NotImplementedError):
        main.serve([])