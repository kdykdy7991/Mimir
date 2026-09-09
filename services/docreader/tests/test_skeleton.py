"""Package skeleton checks: importable, versioned, and a real service entrypoint
(serve/main) is present without starting a server."""

from __future__ import annotations

from docreader import __version__


def test_package_version_present() -> None:
    assert isinstance(__version__, str)
    assert __version__


def test_config_module_importable() -> None:
    import docreader.config  # noqa: F401
    import docreader.parser  # noqa: F401
    import docreader.models  # noqa: F401
    import docreader.utils  # noqa: F401


def test_service_entrypoint_exists() -> None:
    from docreader import main as main_mod
    # serve() is a real (blocking) function now; just confirm it is present
    # without starting a server.
    assert callable(main_mod.serve)
    assert callable(main_mod.main)