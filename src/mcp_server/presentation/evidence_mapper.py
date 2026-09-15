"""
Central MCP evidence mapping (Task 02.2).

Single place that turns application-layer evidence into MCP structured
content. Rules fixed by the task book:

* the application layer never imports this module — dependency direction
  is application contract → MCP presentation only;
* no database, client, embedding or retrieval access: pure functions;
* legacy tool responses stay byte/structurally compatible (same keys,
  same aliases, same markdown); new v1 fields are opt-in via the
  :class:`~src.application.contracts.EvidenceV1` mapping;
* the legacy single ``score`` is NEVER expanded into fake per-stage
  scores — only the stage the legacy pipeline actually tagged
  (``source_type``) is populated, everything else stays ``null``;
* absolute paths, credentials, Authorization headers and tracebacks are
  redacted from free-text v1 fields before they leave the boundary.

The legacy query formatter delegates here so there is one mapping
implementation; Task 04 can delete the legacy row shape once new tools
own the contract.
"""

from __future__ import annotations

import dataclasses
import re
from typing import TYPE_CHECKING, Any

from src.application.contracts import (
    EvidencePageV1,
    EvidenceScores,
    EvidenceV1,
    ResponseBudget,
    SourceLocator,
    WarningCode,
    WarningV1,
    to_json,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from src.mcp_server.clients.models import (
        Diagnostics,
        EvidenceItem,
        KnowledgeQueryResult,
    )


# ---------------------------------------------------------------------------
# Output sanitization
# ---------------------------------------------------------------------------

_REDACTION = "[REDACTED]"
_PATH_REDACTION = "[path removed]"
_TRACEBACK_REDACTION = "Traceback (most recent call last): [traceback removed]"

# Traceback block ending on a typical ``SomeError: message`` line.
_TRACEBACK_RE = re.compile(
    r"Traceback \(most recent call last\):\n"
    r"(?:.*\n)*?"
    r"[A-Za-z_][\w.]*(?:Error|Exception|Interrupt|ExitStack)[^\n]*",
    re.DOTALL,
)
_BEARER_HEADER_RE = re.compile(
    r"(?im)^(authorization\s*:\s*)(?:bearer\s+)?\S+",
)
_BEARER_INLINE_RE = re.compile(
    r"(?i)(authorization(?:\s+header)?\s*[=:]\s*)(?:bearer\s+)?[A-Za-z0-9._~+/\-=]+",
)
_MCP_KEY_RE = re.compile(r"skdy_mcp_[A-Za-z0-9._\-]+")
_SECRET_ASSIGN_RE = re.compile(
    r"(?i)\b((?:api[_-]?key|access[_-]?token|secret|token|password)"
    r"\s*[=:]\s*)[^\s\"'`]+",
)
# POSIX absolute path with at least two segments ("/a/b"), never after a
# word, "." or "/" so URLs ("https://...") and relative paths survive.
_POSIX_PATH_RE = re.compile(r"(?<![\w./])/(?:[A-Za-z0-9._\-]+/)+[A-Za-z0-9._\-]*")
_WINDOWS_PATH_RE = re.compile(
    r"[A-Za-z]:[\\/](?:[A-Za-z0-9_.\-]+[\\/]?)+",
)


def redact_sensitive(text: Any) -> str:
    """Strip credentials, Authorization headers, tracebacks and absolute
    local paths from free-text output. Coerces non-strings to ``str``.

    The redaction order is fixed: tracebacks first (they may themselves
    contain paths), then headers/keys/secrets, then paths inside what
    remains.
    """
    if text is None:
        return ""
    value = str(text)
    value = _TRACEBACK_RE.sub(_TRACEBACK_REDACTION, value)
    value = _BEARER_HEADER_RE.sub(rf"\g<1>{_REDACTION}", value)
    value = _BEARER_INLINE_RE.sub(rf"\g<1>{_REDACTION}", value)
    value = _MCP_KEY_RE.sub(f"skdy_mcp_{_REDACTION}", value)
    value = _SECRET_ASSIGN_RE.sub(rf"\g<1>{_REDACTION}", value)
    value = _WINDOWS_PATH_RE.sub(_PATH_REDACTION, value)
    value = _POSIX_PATH_RE.sub(_PATH_REDACTION, value)
    return value


def safe_preview(text: Any, limit: int | None = 200) -> str:
    """Return a redacted, length-bounded preview.

    Truncation mirrors the pre-02.2 legacy excerpt (hard cut at
    ``limit - 1`` + ``…``) so delegating the old markdown renderer keeps
    its exact output; ``limit=None`` means unbounded.
    """
    value = redact_sensitive((text or "")).strip() if text else ""
    if not value:
        return ""
    if limit is not None and len(value) > limit:
        return value[: max(limit - 1, 0)].rstrip() + "…"
    return value


# ---------------------------------------------------------------------------
# Legacy query result formatting (single implementation after 02.2)
# ---------------------------------------------------------------------------

LEGACY_EMPTY_HINT = (
    "未找到相关文档。请确认已运行 ingest.py 完成数据入库，"
    "或尝试调整 query / top_k。"
)

_LEGACY_STAGE_BY_SOURCE = {
    "dense": "dense",
    "sparse": "sparse",
    "fusion": "fusion",
    "rerank": "rerank",
}


def legacy_evidence_row(item: EvidenceItem) -> dict[str, Any]:
    """Exact pre-02.2 structured row for ``query_knowledge_hub``."""
    return {
        "rank": item.rank,
        "chunk_id": item.chunk_id,
        "document_id": item.document_id,
        "title": item.title,
        "source": item.source,
        "page": item.page,
        "score": item.score,
        "text": item.text,
    }


def _diagnostics_dict(diagnostics: Diagnostics) -> dict[str, Any]:
    return {
        "degraded": diagnostics.degraded,
        "reasons": list(diagnostics.reasons),
        "trace_id": diagnostics.trace_id,
    }


def query_structured(result: KnowledgeQueryResult) -> dict[str, Any]:
    """Build the legacy ``query_knowledge_hub`` structured payload.

    Keys, aliases (``n_results``/``citations``) and the empty-result
    shape are identical to the pre-02.2 formatter — this is the
    byte-compatibility entry point used by the live tool.
    """
    if not result.evidence:
        return {
            "query": result.query,
            "collection": result.collection,
            "count": 0,
            "evidence": [],
            "diagnostics": _diagnostics_dict(result.diagnostics),
            "n_results": 0,
            "citations": [],
        }
    return {
        "query": result.query,
        "collection": result.collection,
        "count": result.count,
        "evidence": [legacy_evidence_row(item) for item in result.evidence],
        "diagnostics": _diagnostics_dict(result.diagnostics),
        # Compatibility window.
        "n_results": result.n_results,
        "citations": result.citations,
    }


def query_markdown(result: KnowledgeQueryResult) -> str:
    """Build the legacy human-readable markdown for a query result."""
    if not result.evidence:
        return LEGACY_EMPTY_HINT
    rows = []
    for item in result.evidence:
        header = f"**[{item.rank}] {item.source}**"
        if item.page is not None:
            header += f" (page {item.page})"
        rows.append((header, safe_preview(item.text, 200)))
    body = "\n\n---\n\n".join(f"{h}\n\n{t}" for h, t in rows)
    refs = ["", "## References", ""]
    for item in result.evidence:
        meta = f"p.{item.page}" if item.page is not None else "n/a"
        refs.append(
            f"[{item.rank}] `{item.chunk_id}` — {item.source} "
            f"({meta}, score={item.score:.4f})",
        )
    return body + "\n".join(refs)


def format_query_result(result: KnowledgeQueryResult) -> tuple[str, dict[str, Any]]:
    """Format a legacy query result as ``(markdown, structured)``."""
    return query_markdown(result), query_structured(result)


# ---------------------------------------------------------------------------
# v1 mapping (new contract; future tools consume this)
# ---------------------------------------------------------------------------

def evidence_v1_from_legacy(
    item: EvidenceItem,
    *,
    collection_id: str,
    query: str,
    preview_chars: int | None = 200,
) -> EvidenceV1:
    """Map one legacy EvidenceItem onto the v1 evidence contract.

    Only the stage named by ``source_type`` (dense/sparse/fusion/rerank)
    receives ``score``; the other three scores stay ``null``. An
    unrecognized ``source_type`` produces all-null scores rather than a
    fabricated value. Versions, parents and assets are never guessed.
    """
    stage = _LEGACY_STAGE_BY_SOURCE.get(item.source_type)
    scores = EvidenceScores(**({stage: float(item.score)} if stage else {}))
    locator = SourceLocator(
        kind="page" if item.page is not None else "chunk",
        page=item.page,
    )
    preview = safe_preview(item.text, preview_chars)
    return EvidenceV1(
        collection_id=collection_id,
        document_id=item.document_id,
        chunk_id=item.chunk_id,
        content_type="text",
        source_locator=locator,
        scores=scores,
        matched_queries=(query,),
        title=item.title or None,
        content=None,
        content_preview=preview or None,
    )


def v1_evidence_row(evidence: EvidenceV1) -> dict[str, Any]:
    """JSON-safe v1 evidence row for future MCP structured output."""
    return evidence.to_dict()


def _trim_to(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    trimmed = safe_preview(value, max(limit, 0))
    return trimmed or None


def _page_json_length(rows: list[EvidenceV1], **page_kwargs: Any) -> int:
    page = EvidencePageV1(results=tuple(rows), **page_kwargs)
    return len(to_json(page.to_dict()))


def _envelope_overhead(rows: list[EvidenceV1], **page_kwargs: Any) -> int:
    """Length of a page with all free text empty AND the truncation
    warning present — the conservative fixed cost every bounded page
    must fit inside before any row text is allowed back."""
    probe = EvidencePageV1(
        results=tuple(rows),
        truncated_results=True,
        truncated_characters=True,
        warnings=(WarningV1(
            code=WarningCode.TRUNCATED,
            message="response truncated to fit server response budget",
        ),),
        **page_kwargs,
    )
    return len(to_json(probe.to_dict()))


def bound_evidence_page(
    results: Any,
    budget: ResponseBudget,
    *,
    page: int | None = None,
    page_size: int | None = None,
    cursor: str | None = None,
    next_cursor: str | None = None,
    has_next: bool = False,
) -> EvidencePageV1:
    """Apply result/body/structured-char budgets to v1 evidence rows.

    Request-bound violations are rejected upstream (invalid_request);
    response overruns are handled explicitly here: capped/shortened
    output always carries a ``truncated`` warning plus the corresponding
    ``truncated_results`` / ``truncated_characters`` flag. Deterministic:
    rows keep order, free text is cut at equal per-row character budgets
    (preview first, then body), and tail rows are dropped only as a last
    resort.
    """
    rows = [EvidenceV1.from_mapping(item) for item in (results or ())]
    truncated_results = len(rows) > budget.max_evidence_count
    rows = rows[: budget.max_evidence_count]

    # Pass 1 — per-field configured caps (also redacts).
    initially_capped: list[EvidenceV1] = []
    for ev in rows:
        initially_capped.append(dataclasses.replace(
            ev,
            content=_trim_to(ev.content, budget.max_content_chars),
            content_preview=_trim_to(ev.content_preview, budget.max_preview_chars),
        ))
    rows = initially_capped

    def build(
        current: list[EvidenceV1], flags: tuple[bool, bool],
    ) -> EvidencePageV1:
        warnings: list[WarningV1] = []
        if flags[0] or flags[1]:
            message = "response truncated to fit server response budget"
            warnings.append(WarningV1(code=WarningCode.TRUNCATED, message=message))
        return EvidencePageV1(
            results=tuple(current),
            page=page,
            page_size=page_size,
            cursor=cursor,
            next_cursor=next_cursor,
            has_next=has_next,
            truncated_results=flags[0],
            truncated_characters=flags[1],
            warnings=tuple(warnings),
        )

    truncated_characters = False
    kwargs = dict(
        page=page, page_size=page_size, cursor=cursor,
        next_cursor=next_cursor, has_next=has_next,
    )

    # Pass 2 — total structured-char budget. Overhead is measured with all
    # free text empty; the remainder is split equally across rows.
    while rows:
        overhead = _envelope_overhead(rows, **kwargs)
        if overhead > budget.max_structured_chars:
            # Identities/locators + envelope alone overflow — drop tail.
            rows.pop()
            truncated_results = True
            continue
        per_row = max(
            (budget.max_structured_chars - overhead) // max(len(rows), 1),
            0,
        )
        adjusted: list[EvidenceV1] = []
        for ev in rows:
            preview_budget = min(budget.max_preview_chars, per_row)
            body_budget = min(
                budget.max_content_chars,
                max(per_row - preview_budget, 0),
            )
            new_preview = _trim_to(ev.content_preview, preview_budget)
            new_content = _trim_to(ev.content, body_budget)
            if new_preview != ev.content_preview or new_content != ev.content:
                truncated_characters = True
            adjusted.append(dataclasses.replace(
                ev, content=new_content, content_preview=new_preview,
            ))
        candidate = build(
            adjusted,
            (truncated_results, truncated_characters),
        )
        if len(to_json(candidate.to_dict())) <= budget.max_structured_chars:
            return candidate
        # Rounding edge: drop the tail row rather than loop on fractions.
        rows = adjusted[:-1]
        truncated_results = True

    return build(rows, (truncated_results, truncated_characters))


def warnings_from_diagnostics(
    diagnostics: Diagnostics,
) -> tuple[WarningV1, ...]:
    """Translate legacy degradation diagnostics into v1 warnings.

    Rerank-related reasons map to ``rerank_degraded`` (deduplicated);
    other degraded reasons are preserved in the message but only mapped
    to codes the v1 contract defines today.
    """
    if not diagnostics.degraded:
        return ()
    warnings: list[WarningV1] = []
    if any("rerank" in reason.lower() for reason in diagnostics.reasons):
        warnings.append(WarningV1(
            code=WarningCode.RERANK_DEGRADED,
            message="; ".join(diagnostics.reasons) or "rerank stage degraded",
        ))
    return tuple(warnings)


__all__ = [
    "LEGACY_EMPTY_HINT",
    "bound_evidence_page",
    "evidence_v1_from_legacy",
    "format_query_result",
    "legacy_evidence_row",
    "query_markdown",
    "query_structured",
    "redact_sensitive",
    "safe_preview",
    "v1_evidence_row",
    "warnings_from_diagnostics",
]
