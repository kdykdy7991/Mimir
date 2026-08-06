"""
OpenAPI snapshot contract test.

Fails the build when the live FastAPI ``app.openapi()`` output diverges
from the checked-in snapshot at ``docs/openapi/openapi.v0.1.json``.

Workflow when the snapshot legitimately changes (after a contract
review, per v0.1 §6):

1. Modify the code AND the snapshot together in the same commit.
2. Re-run ``python -m scripts.export_openapi`` to refresh the file.
3. Run the test to confirm it now passes.

The test never auto-rewrites the file — a human review is always
required before the snapshot changes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = REPO_ROOT / "docs" / "openapi" / "openapi.v0.2.json"


def test_openapi_snapshot_matches_live_app():
    """The on-disk snapshot must equal the live FastAPI schema."""
    if not SNAPSHOT.exists():
        pytest.fail(
            f"snapshot missing at {SNAPSHOT}. "
            f"Run `python -m scripts.export_openapi` to create it."
        )

    # Importing the app pulls in the whole web_api graph; we want to
    # surface real schema errors here (not in CI later).
    from src.web_api.app import create_app

    app = create_app()
    live = app.openapi()
    stored = json.loads(SNAPSHOT.read_text(encoding="utf-8"))

    assert live == stored, (
        f"OpenAPI snapshot drift detected in {SNAPSHOT.relative_to(REPO_ROOT)}.\n"
        f"Either (a) the code is inconsistent with the contract — fix the code, or\n"
        f"(b) the contract changed and was reviewed — run "
        f"`python -m scripts.export_openapi` to refresh."
    )


def test_snapshot_has_expected_top_level_keys():
    """Sanity check the snapshot — guards against empty/partial exports."""
    schema = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert "openapi" in schema
    assert "info" in schema
    assert "paths" in schema
    assert "components" in schema

    # 14 spec endpoints spread across 11 unique paths (some paths have
    # multiple methods: collections, collections/{id}, collections/{id}/documents,
    # documents/{id}). /images/{image_id} adds one more. We assert on
    # the *endpoint* count to keep the test stable across refactors
    # that group routes differently.
    endpoint_count = sum(
        len(methods) for methods in schema["paths"].values()
    )
    assert endpoint_count >= 15, (
        f"expected at least 15 endpoints (14 spec + /images), got {endpoint_count}"
    )

    # The 14 spec endpoints must all be present.
    required_paths = {
        "/api/v1/system/info", "/api/v1/system/health",
        "/api/v1/collections", "/api/v1/collections/{collection_id}",
        "/api/v1/collections/{collection_id}/documents",
        "/api/v1/documents/{document_id}", "/api/v1/tasks/{task_id}",
        "/api/v1/collections/{collection_id}/queries",
        "/api/v1/queries/{query_id}/trace",
        "/api/v1/ingestions/{ingestion_id}/trace",
        "/api/v1/images/{image_id}",
    }
    missing = required_paths - set(schema["paths"].keys())
    assert not missing, f"missing required paths in snapshot: {missing}"
