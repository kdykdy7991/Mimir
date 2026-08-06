"""
Export the v0.2 OpenAPI schema and per-endpoint example files.

Why a script (not just `curl http://127.0.0.1:8766/openapi.json`):

- Reproducible from a clean checkout, no server needed.
- Single source of truth for both the JSON snapshot checked into git
  and the example files under ``docs/openapi/examples/``.
- Backs the CI snapshot test that fails the build when the live
  FastAPI output drifts from the checked-in schema.

Usage::

    python -m scripts.export_openapi            # writes docs/openapi/openapi.v0.2.json
    python -m scripts.export_openapi --check    # exit 1 if drift

The ``--check`` mode is what the CI test uses.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "docs" / "openapi" / "openapi.v0.2.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export Web API OpenAPI schema to JSON.")
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT,
        help=f"Output file (default: {DEFAULT_OUTPUT.relative_to(REPO_ROOT)}).",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Exit non-zero if the on-disk snapshot differs (CI mode).",
    )
    parser.add_argument(
        "--indent", type=int, default=2, help="JSON indent (default: 2).",
    )
    args = parser.parse_args(argv)

    # Imported lazily so a missing dep doesn't crash the script's --help.
    from src.web_api.app import create_app

    app = create_app()
    schema = app.openapi()

    if args.check:
        if not args.output.exists():
            print(f"FAIL: {args.output} does not exist", file=sys.stderr)
            return 1
        existing = json.loads(args.output.read_text(encoding="utf-8"))
        if existing != schema:
            print(
                f"FAIL: OpenAPI snapshot drift detected in {args.output}.\n"
                f"Re-run `python -m scripts.export_openapi` to refresh.",
                file=sys.stderr,
            )
            return 1
        print(f"OK: {args.output} matches live schema ({len(schema.get('paths', {}))} paths)")
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(schema, indent=args.indent, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    n_paths = len(schema.get("paths", {}))
    n_components = len(schema.get("components", {}).get("schemas", {}))
    print(f"Wrote {args.output} ({n_paths} paths, {n_components} schemas)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
