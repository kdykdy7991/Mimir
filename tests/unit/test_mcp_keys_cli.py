"""
Unit tests for ``scripts/mcp_keys.py`` — the key-management CLI.

Exercises each subcommand through the script's ``main(argv)`` entry
point with an isolated ``--data-dir``, then verifies both the printed
output and the on-disk database. Covers output secrecy, exit codes and
error handling (PRD §4.3).
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import pytest

from scripts.mcp_keys import (
    build_parser,
    cmd_create,
    cmd_list,
    cmd_revoke,
    cmd_rotate,
    main,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _db_rows(db_path: Path) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM mcp_api_keys").fetchall()
    finally:
        conn.close()


def _parse(*argv: str) -> argparse.Namespace:
    return build_parser().parse_args(list(argv))


class _Capture:
    """Lightweight capsys-less stdout capture for direct cmd_* calls."""

    def __init__(self) -> None:
        import io
        self.buffer = io.StringIO()
        import contextlib
        self._ctx = contextlib.redirect_stdout(self.buffer)

    def __enter__(self):
        self._ctx.__enter__()
        return self

    def __exit__(self, *exc):
        self._ctx.__exit__(*exc)
        return False

    @property
    def out(self) -> str:
        return self.buffer.getvalue()


# ---------------------------------------------------------------------------
# CLI structure
# ---------------------------------------------------------------------------

class TestParser:
    def test_requires_subcommand(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args([])

    def test_create_parses_collections_and_data_dir(self) -> None:
        args = _parse("create", "--name", "agent-a", "--collections", "hr,policy",
                      "--data-dir", "./scratch")
        assert args.command == "create"
        assert args.name == "agent-a"
        assert args.collections == "hr,policy"
        assert args.data_dir == "./scratch"

    def test_list_revive_rotate_parse(self) -> None:
        for argv in (
            ["list"],
            ["revoke", "--name", "agent-a"],
            ["rotate", "--name", "agent-a"],
        ):
            assert build_parser().parse_args(argv).command == argv[0]

    def test_revoke_requires_name(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args(["revoke"])


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------

class TestCliCreate:
    def test_create_outputs_key_once_and_metadata(self, tmp_path) -> None:
        with _Capture() as cap:
            rc = cmd_create(_parse(
                "create", "--name", "agent-a", "--collections", "hr,policy",
                "--data-dir", str(tmp_path),
            ))
        assert rc == 0
        assert "API key created. Store it now; it cannot be shown again." in cap.out
        assert "name: agent-a" in cap.out
        assert "allowed_collections: hr, policy" in cap.out
        # The key line is present exactly once and has the right shape.
        lines = [ln for ln in cap.out.splitlines() if ln.startswith("key: ")]
        assert len(lines) == 1
        raw = lines[0][len("key: "):]
        assert raw.startswith("skdy_mcp_")
        assert raw.count(".") == 1

        # The database holds only the digest, never the raw key.
        rows = _db_rows(tmp_path / "db" / "mcp_access.db")
        assert len(rows) == 1
        assert raw not in rows[0]["secret_digest"]

    def test_create_empty_collections_is_usage_error(self, tmp_path, capsys) -> None:
        rc = cmd_create(_parse(
            "create", "--name", "agent-a", "--collections", "  , ,",
            "--data-dir", str(tmp_path),
        ))
        captured = capsys.readouterr()
        assert rc == 2
        assert "error" in captured.err.lower()
        assert _db_rows(tmp_path / "db" / "mcp_access.db") == []

    def test_create_duplicate_name_fails(self, tmp_path, capsys) -> None:
        assert cmd_create(_parse(
            "create", "--name", "agent-a", "--collections", "hr",
            "--data-dir", str(tmp_path),
        )) == 0
        rc = cmd_create(_parse(
            "create", "--name", "agent-a", "--collections", "hr",
            "--data-dir", str(tmp_path),
        ))
        captured = capsys.readouterr()
        assert rc == 1
        assert "error" in captured.err.lower()

    def test_create_wildcard_rejected(self, tmp_path, capsys) -> None:
        rc = cmd_create(_parse(
            "create", "--name", "agent-a", "--collections", "*",
            "--data-dir", str(tmp_path),
        ))
        captured = capsys.readouterr()
        assert rc == 2
        assert "error" in captured.err.lower()


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

class TestCliList:
    def test_list_never_prints_key_or_digest(self, tmp_path) -> None:
        with _Capture() as create_cap:
            cmd_create(_parse(
                "create", "--name", "agent-a", "--collections", "hr",
                "--data-dir", str(tmp_path),
            ))
        raw = [ln for ln in create_cap.out.splitlines() if ln.startswith("key: ")][0][len("key: "):]

        with _Capture() as cap:
            rc = cmd_list(_parse("list", "--data-dir", str(tmp_path)))
        assert rc == 0
        assert "agent-a" in cap.out
        assert "hr" in cap.out
        assert raw not in cap.out
        # No secret-shaped token anywhere in the output.
        assert "skdy_mcp_" not in cap.out
        rows = _db_rows(tmp_path / "db" / "mcp_access.db")
        assert rows[0]["secret_digest"] not in cap.out

    def test_list_empty_is_graceful(self, tmp_path, capsys) -> None:
        with _Capture() as cap:
            rc = cmd_list(_parse("list", "--data-dir", str(tmp_path)))
        assert rc == 0
        assert "No API keys." in cap.out

    def test_list_shows_revoked_state(self, tmp_path) -> None:
        cmd_create(_parse(
            "create", "--name", "agent-a", "--collections", "hr",
            "--data-dir", str(tmp_path),
        ))
        cmd_revoke(_parse("revoke", "--name", "agent-a", "--data-dir", str(tmp_path)))
        with _Capture() as cap:
            cmd_list(_parse("list", "--data-dir", str(tmp_path)))
        assert "agent-a" in cap.out
        assert "no" in cap.out  # enabled column shows "no"


# ---------------------------------------------------------------------------
# revoke
# ---------------------------------------------------------------------------

class TestCliRevoke:
    def test_revoke_disables_key(self, tmp_path) -> None:
        cmd_create(_parse(
            "create", "--name", "agent-a", "--collections", "hr",
            "--data-dir", str(tmp_path),
        ))
        with _Capture() as cap:
            rc = cmd_revoke(_parse("revoke", "--name", "agent-a", "--data-dir", str(tmp_path)))
        assert rc == 0
        assert "Revoked key 'agent-a'." in cap.out
        rows = _db_rows(tmp_path / "db" / "mcp_access.db")
        assert rows[0]["enabled"] == 0
        assert rows[0]["revoked_at"] is not None

    def test_revoke_unknown_key_errors(self, tmp_path, capsys) -> None:
        rc = cmd_revoke(_parse("revoke", "--name", "nobody", "--data-dir", str(tmp_path)))
        captured = capsys.readouterr()
        assert rc == 1
        assert "error" in captured.err.lower()


# ---------------------------------------------------------------------------
# rotate
# ---------------------------------------------------------------------------

class TestCliRotate:
    def test_rotate_prints_new_key_once(self, tmp_path) -> None:
        with _Capture() as create_cap:
            cmd_create(_parse(
                "create", "--name", "agent-a", "--collections", "hr,policy",
                "--data-dir", str(tmp_path),
            ))
        old_raw = [ln for ln in create_cap.out.splitlines() if ln.startswith("key: ")][0][len("key: "):]

        with _Capture() as cap:
            rc = cmd_rotate(_parse("rotate", "--name", "agent-a", "--data-dir", str(tmp_path)))
        assert rc == 0
        assert "previous key is now invalid" in cap.out
        lines = [ln for ln in cap.out.splitlines() if ln.startswith("key: ")]
        assert len(lines) == 1
        new_raw = lines[0][len("key: "):]
        assert new_raw != old_raw

        # Exactly one key row remains; digest matches the NEW secret.
        rows = _db_rows(tmp_path / "db" / "mcp_access.db")
        assert len(rows) == 1
        new_secret = new_raw[len("skdy_mcp_"):].split(".", 1)[1]
        import hashlib
        assert rows[0]["secret_digest"] == hashlib.sha256(new_secret.encode()).hexdigest()

    def test_rotate_unknown_key_errors(self, tmp_path, capsys) -> None:
        rc = cmd_rotate(_parse("rotate", "--name", "nobody", "--data-dir", str(tmp_path)))
        captured = capsys.readouterr()
        assert rc == 1
        assert "error" in captured.err.lower()


# ---------------------------------------------------------------------------
# main() entry point
# ---------------------------------------------------------------------------

class TestMainEntrypoint:
    def test_main_create_via_subprocess_style_argv(self, tmp_path) -> None:
        with _Capture() as cap:
            rc = main(["create", "--name", "agent-a", "--collections", "hr",
                       "--data-dir", str(tmp_path)])
        assert rc == 0
        assert "skdy_mcp_" in cap.out

    def test_main_returns_nonzero_on_error(self, tmp_path, capsys) -> None:
        rc = main(["revoke", "--name", "nobody", "--data-dir", str(tmp_path)])
        assert rc == 1
