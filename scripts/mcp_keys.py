#!/usr/bin/env python3
"""
MCP API key management CLI.

Creates, lists, revokes and rotates the API keys used for
``streamable-http`` MCP authentication (PRD
``docs/prd-mcp-api-key-collection-access.md`` §4.3).

The full key is printed exactly once — at creation/rotation — and can
never be recovered afterwards. ``list`` and ``revoke`` print metadata
only (never the secret or its digest).

Examples
--------
    # Create a key for agent-a with access to the hr + policy libraries
    python scripts/mcp_keys.py create --name agent-a --collections hr,policy

    # Show metadata for all keys
    python scripts/mcp_keys.py list

    # Immediately revoke agent-a's key
    python scripts/mcp_keys.py revoke --name agent-a

    # Issue a fresh key for agent-a and invalidate the previous one
    python scripts/mcp_keys.py rotate --name agent-a

All commands accept ``--data-dir`` (default ``./data``); the database
lives at ``<data-dir>/db/mcp_access.db``.
"""

from __future__ import annotations

import argparse
import sys
from datetime import timezone
from pathlib import Path

# Make ``python scripts/mcp_keys.py`` work from any CWD.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.mcp_server.auth import (  # noqa: E402
    ApiKeyMetadata,
    ApiKeyService,
    DuplicateKeyNameError,
    InvalidCollectionWhitelistError,
    KeyNotFoundError,
)

_EXIT_USAGE = 2
_EXIT_ERROR = 1


def _service(data_dir: str) -> ApiKeyService:
    db_path = Path(data_dir) / "db" / "mcp_access.db"
    return ApiKeyService(db_path=db_path)


def _fmt_ts(value: object) -> str:
    """Render a timestamp as UTC seconds-precision, or ``-`` if unset."""
    if value is None:
        return "-"
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _print_key_once(raw_key: str, metadata: ApiKeyMetadata, *, header: str) -> None:
    """Print a full key exactly once, with its metadata."""
    print(header)
    print(f"name: {metadata.name}")
    print(f"key: {raw_key}")
    print("allowed_collections: " + ", ".join(sorted(metadata.allowed_collections)))


def cmd_create(args: argparse.Namespace) -> int:
    collections = {c.strip() for c in args.collections.split(",") if c.strip()}
    try:
        raw_key, metadata = _service(args.data_dir).create_key(
            name=args.name, allowed_collections=collections,
        )
    except InvalidCollectionWhitelistError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _EXIT_USAGE
    except DuplicateKeyNameError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _EXIT_ERROR
    _print_key_once(
        raw_key, metadata,
        header="API key created. Store it now; it cannot be shown again.",
    )
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    keys = _service(args.data_dir).list_keys()
    if not keys:
        print("No API keys.")
        return 0
    print("name\tenabled\tkey_id\tcreated_at (UTC)\trevoked_at (UTC)\tcollections")
    for key in keys:
        print(
            f"{key.name}\t"
            f"{'yes' if key.enabled else 'no'}\t"
            f"{key.key_id}\t"
            f"{_fmt_ts(key.created_at)}\t"
            f"{_fmt_ts(key.revoked_at)}\t"
            f"{', '.join(sorted(key.allowed_collections))}"
        )
    return 0


def cmd_revoke(args: argparse.Namespace) -> int:
    try:
        metadata = _service(args.data_dir).revoke_key(name=args.name)
    except KeyNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _EXIT_ERROR
    print(f"Revoked key '{metadata.name}'.")
    return 0


def cmd_rotate(args: argparse.Namespace) -> int:
    try:
        raw_key, metadata = _service(args.data_dir).rotate_key(name=args.name)
    except KeyNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _EXIT_ERROR
    _print_key_once(
        raw_key, metadata,
        header=(
            "API key rotated. The previous key is now invalid. "
            "Store it now; it cannot be shown again."
        ),
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcp_keys",
        description="Manage MCP API keys (create, list, revoke, rotate).",
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--data-dir", default="./data",
        help=(
            "Data directory; the DB lives at <data-dir>/db/mcp_access.db "
            "(default: ./data)."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create", parents=[common], help="Create a new API key.")
    p_create.add_argument(
        "--name", required=True, help="Human-readable key name (unique).",
    )
    p_create.add_argument(
        "--collections", required=True,
        help="Comma-separated collection whitelist, e.g. hr,policy.",
    )
    p_create.set_defaults(func=cmd_create)

    p_list = sub.add_parser("list", parents=[common], help="List key metadata (no secrets).")
    p_list.set_defaults(func=cmd_list)

    p_revoke = sub.add_parser("revoke", parents=[common], help="Revoke a key by name.")
    p_revoke.add_argument("--name", required=True, help="Name of the key to revoke.")
    p_revoke.set_defaults(func=cmd_revoke)

    p_rotate = sub.add_parser("rotate", parents=[common], help="Rotate a key by name.")
    p_rotate.add_argument("--name", required=True, help="Name of the key to rotate.")
    p_rotate.set_defaults(func=cmd_rotate)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
