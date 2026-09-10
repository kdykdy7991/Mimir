"""Delimiter-free collection-scope encoding for the internal HTTP boundary.

Collection names may legally contain commas (``CollectionCreateRequest``
only bounds length; ``key_service`` does not forbid commas). A
comma-joined header would therefore expand a single grant ``"finance,hr"``
into two grants ``finance`` and ``hr`` on the receiving side — a P0 scope
escalation. We instead encode the granted set as the Base64URL of a JSON
array, which is delimiter-free and unambiguous.

Both the sending ``HttpRagReadOnlyClient`` and the receiving internal API
must use these exact helpers so the wire format stays in lockstep.
"""

from __future__ import annotations

import base64
import json


def encode_scope_header(collections: object) -> str | None:
    """Encode an iterable of collection names into a header value.

    Returns ``None`` for an empty collection — the caller should then omit
    the header so the receiving side can treat it strictly as deny-all.
    """
    names = sorted({str(c) for c in collections})
    if not names:
        return None
    raw = json.dumps(names, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_scope_header(value: str) -> tuple[str, ...]:
    """Decode a header produced by :func:`encode_scope_header`.

    Raises ``ValueError`` on any malformed or non-string payload so the
    receiver can fail closed (never silently widen scope).
    """
    padded = value + "=" * (-len(value) % 4)
    raw = base64.urlsafe_b64decode(padded.encode("ascii"))
    names = json.loads(raw.decode("utf-8"))
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise ValueError("invalid scope header")
    return tuple(names)