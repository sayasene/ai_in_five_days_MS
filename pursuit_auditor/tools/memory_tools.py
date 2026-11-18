"""Memory & Gate 4 lineage tools backed by persistent vector precedent retrieval."""

from __future__ import annotations

from typing import Any

from google.adk.tools import ToolContext
from pydantic import ValidationError

from pursuit_auditor.memory.vector_store import get_precedent_store
from pursuit_auditor.observability.logging import get_logger
from pursuit_auditor.observability.tracing import traced_span
from pursuit_auditor.schemas import (
    LineageCitationRequest,
    NumbersLineageRow,
    PrecedentSearchRequest,
    PrecedentSearchResponse,
    ToolErrorResponse,
)

logger = get_logger(__name__)


def retrieve_historical_audit_precedents(
    query: str,
    gate_filter: str | None = None,
    top_k: int = 3,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Query the persistent vector precedent database for prior audit rulings and patterns.

    Searches SQLite-persisted vector embeddings of historical pursuit audits so
    gate specialists apply consistent precedent across engagements and sessions.

    Args:
        query: Natural-language description of the risk, workstream, or failure
            pattern to match (for example, `"partner deck internal rate card leak"`).
        gate_filter: Optional gate scope (`"Gate 1"`, `"Gate 2"`, `"Gate 3"`,
            or `"Gate 4"`); pass `None` to search across all gates.
        top_k: Maximum number of matching precedents to return (1 to 10).

    Returns:
        A JSON-serializable `PrecedentSearchResponse` dictionary with ranked
        `matches` (`precedent_id`, `gate`, `pattern_title`, `summary`,
        `resolution`, `similarity_score`), or `ToolErrorResponse` on invalid args.
    """
    with traced_span(
        "audit.tool.retrieve_historical_audit_precedents",
        {"search.top_k": top_k, "search.gate_filter": gate_filter or "all"},
    ):
        try:
            req = PrecedentSearchRequest(query=query, gate_filter=gate_filter, top_k=top_k)  # type: ignore[arg-type]
        except ValidationError as exc:
            return ToolErrorResponse(
                error_code="PRECEDENT_QUERY_VALIDATION_ERROR",
                recoverable=True,
                message=f"Invalid precedent query arguments ({len(exc.errors())} issue(s)).",
                missing_fields=[".".join(str(p) for p in e.get("loc", ())) for e in exc.errors()],
                recovery_instructions=[
                    "Provide a query of at least 3 characters.",
                    "Set `gate_filter` to one of 'Gate 1', 'Gate 2', 'Gate 3', 'Gate 4', or omit it.",
                    "Keep `top_k` between 1 and 10.",
                ],
                retry_example={
                    "query": "summary vs deep-dive funding drift",
                    "gate_filter": "Gate 1",
                    "top_k": 3,
                },
            ).model_dump()

        store = get_precedent_store()
        matches = store.search_precedents(
            query=req.query,
            gate_filter=req.gate_filter,
            top_k=req.top_k,
        )
        response = PrecedentSearchResponse(
            query=req.query,
            backend="sqlite_vector_precedent_store",
            matches=matches,
        )

        if tool_context is not None:
            tool_context.state["retrieved_precedent_ids"] = [m.precedent_id for m in matches]

        logger.info(
            "audit_precedents_retrieved",
            match_count=len(matches),
            gate_filter=req.gate_filter or "all",
        )
        return response.model_dump()


def record_numbers_lineage_citation(
    metric: str,
    value: str,
    exact_source: str,
    derivation: str,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Register a verified source-of-truth metric citation in persistent session state.

    Used during Gate 1 and Gate 4 to accumulate an auditable Numbers Lineage
    table mapping every headline figure in the deliverable back to its exact
    workbook, tab, column, and arithmetic formula.

    Args:
        metric: Metric name (e.g., `"Total Cost to Migrate/Implement"`).
        value: Formatted figure displayed in the deliverable (e.g., `"$10,000,000"`).
        exact_source: Authoritative workbook, tab, and column citation.
        derivation: Explicit formula or arithmetic derivation backing the figure.

    Returns:
        Dictionary with `status="recorded"` and the validated `NumbersLineageRow`,
        or `ToolErrorResponse` with recovery guidance if any citation field is missing.
    """
    with traced_span("audit.tool.record_numbers_lineage_citation", {"lineage.metric": metric}):
        try:
            req = LineageCitationRequest(
                metric=metric,
                value=value,
                exact_source=exact_source,
                derivation=derivation,
            )
        except ValidationError as exc:
            return ToolErrorResponse(
                error_code="LINEAGE_CITATION_INVALID",
                recoverable=True,
                message="Numbers lineage citation requires non-empty metric, value, exact_source, and derivation.",
                missing_fields=[".".join(str(p) for p in e.get("loc", ())) for e in exc.errors()],
                recovery_instructions=[
                    "Supply all four fields so every figure can be defended live in executive review.",
                    "If the source cannot be verified, flag the figure as a BLOCKING Gate 1 issue instead.",
                ],
                retry_example={
                    "metric": "Steady-State ARR",
                    "value": "$15,000,000",
                    "exact_source": "Northstar_DealModel_v4_Final -> Post_Discount_InScope_Summary (Column G)",
                    "derivation": "Sum of in-scope post-discount workstream ARR",
                },
            ).model_dump()

        row = NumbersLineageRow(**req.model_dump())
        if tool_context is not None:
            lineage = list(tool_context.state.get("numbers_lineage", []))
            lineage.append(row.model_dump())
            tool_context.state["numbers_lineage"] = lineage

        logger.info("numbers_lineage_recorded", metric=row.metric)
        return {"status": "recorded", "lineage_row": row.model_dump()}
