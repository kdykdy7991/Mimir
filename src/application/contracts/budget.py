"""Response budget and request limits (Task 02 §02.3).

One transport-neutral source of truth for the bounds shared by tool
schemas, runtime validation and output truncation:

* request bounds — query length, top-k, page size;
* response bounds — maximum evidence rows, single-evidence body/preview
  length and total structured-content characters;
* Task 05 bounds — alternate-query count / total characters, collection
  count and aggregate candidate work.

The defaults are frozen to the exact limits the five legacy tools
already expose (query 1..2000, top_k 1..50 default 10, page_size
1..50 default 20), so adopting this budget changes no public default.

Enforcement policy is fixed by construction:
**request-bound violations raise :class:`ContractError` (stable
``invalid_request``); response-bound overrun is handled in the output
layer with an explicit ``truncated`` warning — never silently.**
"""

from __future__ import annotations

from dataclasses import dataclass

from src.application.contracts.serialization import ContractError


@dataclass(frozen=True)
class ResponseBudget:
    """Immutable request/response budget.

    Constructed once from validated settings at startup; tools and
    mappers receive it explicitly or via the MCP active-budget holder.
    """

    query_max_length: int = 2000
    top_k_default: int = 10
    top_k_max: int = 50
    page_size_default: int = 20
    page_size_max: int = 50
    max_evidence_count: int = 50
    max_structured_chars: int = 200_000
    max_content_chars: int = 8_000
    max_preview_chars: int = 200
    # Task 05 multi-query / multi-collection request limits.
    alternate_query_max_count: int = 3
    alternate_query_total_max_chars: int = 3_000
    collection_max_count: int = 20
    search_candidate_work_max: int = 1_000

    def __post_init__(self) -> None:
        def positive(name: str) -> None:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ContractError(f"budget.{name} must be a positive integer")

        for name in (
            "query_max_length", "top_k_default", "top_k_max",
            "page_size_default", "page_size_max", "max_evidence_count",
            "max_structured_chars", "max_content_chars", "max_preview_chars",
            "alternate_query_max_count", "alternate_query_total_max_chars",
            "collection_max_count", "search_candidate_work_max",
        ):
            positive(name)
        if self.top_k_default > self.top_k_max:
            raise ContractError("budget.top_k_default must be <= top_k_max")
        if self.page_size_default > self.page_size_max:
            raise ContractError(
                "budget.page_size_default must be <= page_size_max",
            )
        if self.max_evidence_count < self.top_k_max:
            raise ContractError(
                "budget.max_evidence_count must be >= top_k_max",
            )
        if self.max_preview_chars > self.max_content_chars:
            raise ContractError(
                "budget.max_preview_chars must be <= max_content_chars",
            )
        if self.max_content_chars > self.max_structured_chars:
            raise ContractError(
                "budget.max_content_chars must be <= max_structured_chars",
            )
        if self.alternate_query_total_max_chars < self.query_max_length:
            raise ContractError(
                "budget.alternate_query_total_max_chars must be >= "
                "query_max_length",
            )

    # -- Request validation (failures become stable invalid_request) ------

    def check_query_length(self, query: str) -> None:
        if not isinstance(query, str) or not query.strip():
            raise ContractError(
                "'query' is required and must be a non-empty string",
            )
        if len(query) > self.query_max_length:
            raise ContractError(
                f"'query' exceeds the {self.query_max_length}-character limit",
            )

    def check_top_k(self, value: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ContractError("'top_k' must be an integer")
        if not 1 <= value <= self.top_k_max:
            raise ContractError(
                f"'top_k' must be an integer between 1 and {self.top_k_max}",
            )

    def check_page(self, value: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ContractError("'page' must be an integer")
        if value < 1:
            # Legacy wording, kept byte-identical at default budget.
            raise ContractError("page must be >= 1")

    def check_page_size(self, value: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ContractError("'page_size' must be an integer")
        if not 1 <= value <= self.page_size_max:
            # Legacy wording, kept byte-identical at default budget.
            raise ContractError(
                f"page_size must be between 1 and {self.page_size_max}",
            )
