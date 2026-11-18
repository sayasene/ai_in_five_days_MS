"""Gate 1 tools: Authoritative deal baseline lookup and financial/rate-card reconciliation."""

from __future__ import annotations

import json
from typing import Any

from google.adk.tools import ToolContext
from pydantic import ValidationError

from pursuit_auditor.config.settings import get_settings
from pursuit_auditor.observability.logging import get_logger
from pursuit_auditor.observability.tracing import traced_span
from pursuit_auditor.schemas import (
    AuthoritativeBaselineRequest,
    AuthoritativeBaselineResponse,
    FinancialReconciliationReport,
    FinancialReconciliationRequest,
    GateFinding,
    NumbersLineageRow,
    ToolErrorResponse,
)

logger = get_logger(__name__)


def _load_baselines_payload() -> dict[str, Any]:
    """Load the synthetic authoritative deal baselines fixture from disk."""
    settings = get_settings()
    return json.loads(settings.baselines_file.read_text(encoding="utf-8"))


def _format_validation_error(exc: ValidationError, retry_example: dict[str, Any]) -> dict[str, Any]:
    """Convert Pydantic validation failures into guided LLM recovery payloads."""
    missing_fields = [
        ".".join(str(part) for part in err.get("loc", ())) for err in exc.errors()
    ]
    return ToolErrorResponse(
        error_code="SCHEMA_VALIDATION_ERROR",
        recoverable=True,
        message=f"Input arguments failed strict schema validation ({len(exc.errors())} issue(s)).",
        missing_fields=missing_fields,
        recovery_instructions=[
            "Inspect `missing_fields` and correct argument types, names, or value ranges.",
            "Do NOT pass undeclared keys; every tool enforces `extra='forbid'`.",
            "Retry the tool call matching the structure shown in `retry_example`.",
        ],
        retry_example=retry_example,
    ).model_dump()


def fetch_authoritative_deal_baseline(
    deal_id: str,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Retrieve the authoritative deal baseline, source tab citation, and synthetic rate benchmarks.

    Looks up the approved funding envelope, workstream breakdown, migration
    duration, steady-state ARR, and authoritative workbook tab/column for a
    given deal identifier. Strictly read-only.

    Args:
        deal_id: Unique synthetic opportunity identifier (for example,
            `"OPP-SYNTH-2026-001"` for Northstar Retail or
            `"OPP-SYNTH-2026-002"` for Crestline Logistics).

    Returns:
        A JSON-serializable dictionary adhering to `AuthoritativeBaselineResponse`
        on success (`status="success"`, `account_name`, `source_citation`,
        `funding_envelope`, `workstreams`, `rate_card_benchmarks_usd_per_month`),
        or `ToolErrorResponse` (`status="error"`, `recovery_instructions`) if
        the `deal_id` is invalid or unknown.
    """
    with traced_span("audit.tool.fetch_authoritative_deal_baseline", {"deal.id": deal_id}):
        try:
            req = AuthoritativeBaselineRequest(deal_id=deal_id)
        except ValidationError as exc:
            return _format_validation_error(exc, {"deal_id": "OPP-SYNTH-2026-001"})

        payload = _load_baselines_payload()
        deals: dict[str, Any] = payload.get("deals", {})
        if req.deal_id not in deals:
            available = sorted(deals.keys())
            return ToolErrorResponse(
                error_code="DEAL_BASELINE_NOT_FOUND",
                recoverable=True,
                message=f"Deal baseline '{req.deal_id}' was not found in the authoritative store.",
                missing_fields=["deal_id"],
                recovery_instructions=[
                    f"Select a valid deal_id from the authoritative baseline catalog: {available}.",
                    "Confirm the opportunity ID from the deliverable cover slide before retrying.",
                    "Never invent baseline numbers if no authoritative deal record exists.",
                ],
                retry_example={"deal_id": available[0] if available else "OPP-SYNTH-2026-001"},
            ).model_dump()

        deal = deals[req.deal_id]
        benchmarks = payload["rate_card_benchmarks_usd_per_month"]
        source_citation = (
            f"{deal['source_sheet_name']} -> {deal['authoritative_tab']} "
            f"({deal['authoritative_column']})"
        )
        response = AuthoritativeBaselineResponse(
            deal_id=deal["deal_id"],
            account_name=deal["account_name"],
            engagement_title=deal["engagement_title"],
            source_citation=source_citation,
            duration_months=deal["duration_months"],
            total_tcv_usd=deal["total_tcv_usd"],
            steady_state_arr_usd=deal["steady_state_arr_usd"],
            funding_envelope=deal["funding_envelope"],
            workstreams=deal["workstreams"],
            rate_card_benchmarks_usd_per_month=benchmarks,
        )

        if tool_context is not None:
            tool_context.state["active_deal_id"] = response.deal_id
            tool_context.state["active_account_name"] = response.account_name
            tool_context.state["active_source_citation"] = source_citation

        logger.info(
            "authoritative_baseline_fetched",
            deal_id=response.deal_id,
            account_name=response.account_name,
            workstream_count=len(response.workstreams),
        )
        return response.model_dump()


def reconcile_financial_rollups_and_rate_cards(
    deal_id: str,
    reported_summary_total_cost_usd: float,
    reported_summary_gsf_usd: float,
    reported_summary_psf_usd: float,
    reported_summary_duration_months: int,
    reported_source_tab: str,
    workstreams: list[dict[str, Any]],
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Run Gate 1 math, cross-slide rollup, source-tab parity, and rate-card reconciliation.

    Deterministically audits:
    1. Row arithmetic (`GSF (PSO) + PSF (Partner) == Total cost to migrate/implement`).
    2. Executive summary totals vs. sum of deep-dive workstream rows.
    3. Authoritative source-of-truth tab parity (detects legacy or raw tab drift).
    4. Timeline consistency between executive summary duration and deep-dive chevrons.
    5. Consulting FTE-month rate-card alignment against synthetic planning benchmarks.

    Args:
        deal_id: Authoritative synthetic deal identifier (e.g., `"OPP-SYNTH-2026-001"`).
        reported_summary_total_cost_usd: Executive summary total migration/implementation cost in USD.
        reported_summary_gsf_usd: Executive summary consulting funding (GSF/PSO) total in USD.
        reported_summary_psf_usd: Executive summary partner funding (PSF) total in USD.
        reported_summary_duration_months: Executive summary migration timeline in months.
        reported_source_tab: Workbook tab name cited by the deliverable.
        workstreams: List of workstream row dictionaries, each matching
            `WorkstreamFinancialRow` (`rank`, `name`, `role`, `steady_state_arr_usd`,
            `gsf_pso_usd`, `psf_partner_usd`, `total_cost_usd`, `duration_months`,
            `planned_fte_months`, `rate_tier`, `outcome_text`).

    Returns:
        A JSON-serializable `FinancialReconciliationReport` dictionary containing
        `passed`, `computed_totals`, `findings` (`BLOCKING` or `POLISH`), and
        `verified_lineage_rows` for Gate 4 brief synthesis, or `ToolErrorResponse`
        with concrete recovery guidance on invalid inputs.
    """
    with traced_span(
        "audit.tool.reconcile_financial_rollups_and_rate_cards",
        {"deal.id": deal_id, "workstream.count": len(workstreams) if isinstance(workstreams, list) else 0},
    ):
        try:
            req = FinancialReconciliationRequest.model_validate(
                {
                    "deal_id": deal_id,
                    "reported_summary_total_cost_usd": reported_summary_total_cost_usd,
                    "reported_summary_gsf_usd": reported_summary_gsf_usd,
                    "reported_summary_psf_usd": reported_summary_psf_usd,
                    "reported_summary_duration_months": reported_summary_duration_months,
                    "reported_source_tab": reported_source_tab,
                    "workstreams": workstreams,
                }
            )
        except ValidationError as exc:
            return _format_validation_error(
                exc,
                {
                    "deal_id": "OPP-SYNTH-2026-001",
                    "reported_summary_total_cost_usd": 10000000,
                    "reported_summary_gsf_usd": 3200000,
                    "reported_summary_psf_usd": 6800000,
                    "reported_summary_duration_months": 18,
                    "reported_source_tab": "Post_Discount_InScope_Summary",
                    "workstreams": [
                        {
                            "rank": 1,
                            "name": "Program Governance (DE & EA)",
                            "role": "Control Tower",
                            "steady_state_arr_usd": 0,
                            "gsf_pso_usd": 1350000,
                            "psf_partner_usd": 0,
                            "total_cost_usd": 1350000,
                            "duration_months": 18,
                            "planned_fte_months": 9.0,
                            "rate_tier": "governance_senior_fte",
                        }
                    ],
                },
            )

        payload = _load_baselines_payload()
        deals = payload.get("deals", {})
        if req.deal_id not in deals:
            return ToolErrorResponse(
                error_code="DEAL_BASELINE_NOT_FOUND",
                recoverable=True,
                message=f"Cannot reconcile against unknown deal_id '{req.deal_id}'.",
                missing_fields=["deal_id"],
                recovery_instructions=[
                    "Call `fetch_authoritative_deal_baseline` first to verify the exact deal_id.",
                    f"Available synthetic baselines: {sorted(deals.keys())}.",
                ],
                retry_example={"deal_id": sorted(deals.keys())[0]},
            ).model_dump()

        baseline = deals[req.deal_id]
        benchmarks = payload["rate_card_benchmarks_usd_per_month"]
        tolerance = float(benchmarks.get("tolerance_pct", 0.12))
        source_citation = (
            f"{baseline['source_sheet_name']} -> {baseline['authoritative_tab']} "
            f"({baseline['authoritative_column']})"
        )

        findings: list[GateFinding] = []
        finding_seq = 1

        def add_finding(location: str, issue: str, exact_fix: str, severity: str = "BLOCKING") -> None:
            nonlocal finding_seq
            findings.append(
                GateFinding(
                    finding_id=f"G1-{finding_seq:02d}",
                    location=location,
                    gate="Gate 1",
                    severity="BLOCKING" if severity == "BLOCKING" else "POLISH",
                    issue=issue,
                    exact_fix=exact_fix,
                )
            )
            finding_seq += 1

        # 1. Source-of-truth tab parity check
        expected_tab = baseline["authoritative_tab"]
        if req.reported_source_tab.strip().lower() != expected_tab.strip().lower():
            add_finding(
                location="Source Footnote / Funding Table",
                issue=(
                    f"Deliverable cites tab '{req.reported_source_tab}', which does not match "
                    f"authoritative post-discount tab '{expected_tab}'."
                ),
                exact_fix=(
                    f"Re-pull all ARR, GSF, and PSF numbers from '{source_citation}' "
                    "and update source footnotes on every financial slide."
                ),
            )

        # 2. Row-level arithmetic (GSF + PSF == Total Cost) and rate-card consistency
        sum_gsf = 0.0
        sum_psf = 0.0
        sum_total = 0.0
        sum_arr = 0.0
        max_ws_duration = 0

        for ws in req.workstreams:
            sum_gsf += ws.gsf_pso_usd
            sum_psf += ws.psf_partner_usd
            sum_total += ws.total_cost_usd
            sum_arr += ws.steady_state_arr_usd
            max_ws_duration = max(max_ws_duration, ws.duration_months)

            expected_row_total = ws.gsf_pso_usd + ws.psf_partner_usd
            if abs(expected_row_total - ws.total_cost_usd) > 1.0:
                add_finding(
                    location=f"Workstream Table — Row #{ws.rank} ({ws.name})",
                    issue=(
                        f"GSF (${ws.gsf_pso_usd:,.0f}) + PSF (${ws.psf_partner_usd:,.0f}) = "
                        f"${expected_row_total:,.0f}, but row Total Cost shows ${ws.total_cost_usd:,.0f}."
                    ),
                    exact_fix=(
                        f"Update '{ws.name}' Total cost to migrate/implement to "
                        f"${expected_row_total:,.0f} (or reconcile GSF/PSF components)."
                    ),
                )

            if ws.planned_fte_months > 0 and ws.gsf_pso_usd > 0:
                benchmark_rate = float(benchmarks[ws.rate_tier])
                expected_gsf = ws.planned_fte_months * benchmark_rate
                drift_ratio = abs(ws.gsf_pso_usd - expected_gsf) / max(1.0, expected_gsf)
                if drift_ratio > tolerance:
                    add_finding(
                        location=f"Deep-Dive Resourcing — {ws.name}",
                        issue=(
                            f"GSF funding (${ws.gsf_pso_usd:,.0f}) drifts {drift_ratio:.0%} from "
                            f"benchmark math ({ws.planned_fte_months:.1f} FTE-mo x "
                            f"${benchmark_rate:,.0f}/mo = ${expected_gsf:,.0f})."
                        ),
                        exact_fix=(
                            f"Align '{ws.name}' FTE-months or GSF ask to ~${expected_gsf:,.0f} "
                            f"(within +/-{tolerance:.0%} of {ws.rate_tier} synthetic benchmark)."
                        ),
                    )

        # 3. Executive summary rollup vs. workstream sums
        if abs(req.reported_summary_gsf_usd + req.reported_summary_psf_usd - req.reported_summary_total_cost_usd) > 1.0:
            calc = req.reported_summary_gsf_usd + req.reported_summary_psf_usd
            add_finding(
                location="Executive Summary Funding Rollup",
                issue=(
                    f"Summary GSF (${req.reported_summary_gsf_usd:,.0f}) + Summary PSF "
                    f"(${req.reported_summary_psf_usd:,.0f}) = ${calc:,.0f}, conflicting with "
                    f"reported Summary Total Cost (${req.reported_summary_total_cost_usd:,.0f})."
                ),
                exact_fix=f"Correct Executive Summary Total cost to migrate/implement to ${calc:,.0f}.",
            )

        if abs(req.reported_summary_gsf_usd - sum_gsf) > 1.0:
            add_finding(
                location="Cross-Slide Rollup (Executive Summary vs. Deep-Dive)",
                issue=(
                    f"Executive Summary GSF (${req.reported_summary_gsf_usd:,.0f}) does not match "
                    f"sum of deep-dive workstream GSF (${sum_gsf:,.0f})."
                ),
                exact_fix=(
                    f"Reconcile Executive Summary and workstream GSF totals to authoritative "
                    f"baseline ${baseline['funding_envelope']['gsf_pso_usd']:,.0f}."
                ),
            )

        if abs(req.reported_summary_psf_usd - sum_psf) > 1.0:
            add_finding(
                location="Cross-Slide Rollup (Executive Summary vs. Deep-Dive)",
                issue=(
                    f"Executive Summary PSF (${req.reported_summary_psf_usd:,.0f}) does not match "
                    f"sum of deep-dive workstream PSF (${sum_psf:,.0f})."
                ),
                exact_fix=(
                    f"Reconcile Executive Summary and workstream PSF totals to authoritative "
                    f"baseline ${baseline['funding_envelope']['psf_partner_usd']:,.0f}."
                ),
            )

        # 4. Authoritative baseline parity checks
        env = baseline["funding_envelope"]
        if abs(sum_gsf - env["gsf_pso_usd"]) > 1.0 or abs(sum_psf - env["psf_partner_usd"]) > 1.0:
            add_finding(
                location="Authoritative Baseline Parity",
                issue=(
                    f"Deliverable totals (GSF ${sum_gsf:,.0f}, PSF ${sum_psf:,.0f}) diverge from "
                    f"authoritative baseline (GSF ${env['gsf_pso_usd']:,.0f}, PSF ${env['psf_partner_usd']:,.0f})."
                ),
                exact_fix=(
                    f"Update funding table to match {source_citation}: "
                    f"Total ${env['total_migration_cost_usd']:,.0f} = GSF ${env['gsf_pso_usd']:,.0f} + "
                    f"PSF ${env['psf_partner_usd']:,.0f}."
                ),
            )

        if abs(sum_arr - baseline["steady_state_arr_usd"]) > 1.0:
            add_finding(
                location="Steady-State ARR Rollup",
                issue=(
                    f"Workstream ARR sum (${sum_arr:,.0f}) diverges from authoritative steady-state "
                    f"ARR (${baseline['steady_state_arr_usd']:,.0f})."
                ),
                exact_fix=(
                    f"Reconcile workstream ARR entries to sum to ${baseline['steady_state_arr_usd']:,.0f} "
                    f"per {source_citation} (keeping enabling workstreams at $0)."
                ),
            )

        # 5. Summary duration vs. deep-dive duration vs. authoritative baseline duration
        if req.reported_summary_duration_months != max_ws_duration:
            add_finding(
                location="Timeline Consistency (Summary vs. Deep-Dive Chevrons)",
                issue=(
                    f"Summary timeline states {req.reported_summary_duration_months} months while "
                    f"deep-dive workstreams extend to {max_ws_duration} months."
                ),
                exact_fix=(
                    f"Align summary timeline and chevron milestones to the authoritative "
                    f"{baseline['duration_months']}-month schedule."
                ),
            )
        elif req.reported_summary_duration_months != baseline["duration_months"]:
            add_finding(
                location="Timeline Parity vs. Authoritative Baseline",
                issue=(
                    f"Reported duration ({req.reported_summary_duration_months} months) differs "
                    f"from authoritative baseline ({baseline['duration_months']} months)."
                ),
                exact_fix=f"Update timeline slides to {baseline['duration_months']} months.",
            )

        lineage_rows = [
            NumbersLineageRow(
                metric="Total Cost to Migrate/Implement",
                value=f"${env['total_migration_cost_usd']:,.0f}",
                exact_source=source_citation,
                derivation=f"GSF (${env['gsf_pso_usd']:,.0f}) + PSF (${env['psf_partner_usd']:,.0f})",
            ),
            NumbersLineageRow(
                metric="GSF (PSO) Funding Ask",
                value=f"${env['gsf_pso_usd']:,.0f}",
                exact_source=source_citation,
                derivation="Sum of approved workstream consulting FTE-months x synthetic tier rate",
            ),
            NumbersLineageRow(
                metric="PSF (Partner) Funding Ask",
                value=f"${env['psf_partner_usd']:,.0f}",
                exact_source=source_citation,
                derivation="Sum of partner execution workstreams (excluding $0 Control Tower governance)",
            ),
            NumbersLineageRow(
                metric="Steady-State ARR",
                value=f"${baseline['steady_state_arr_usd']:,.0f}",
                exact_source=source_citation,
                derivation=f"In-scope post-discount ARR over {baseline['duration_months']}-month horizon",
            ),
        ]

        blocking = [f for f in findings if f.severity == "BLOCKING"]
        report = FinancialReconciliationReport(
            deal_id=req.deal_id,
            passed=len(blocking) == 0,
            computed_totals={
                "workstream_sum_gsf_usd": sum_gsf,
                "workstream_sum_psf_usd": sum_psf,
                "workstream_sum_total_cost_usd": sum_total,
                "workstream_sum_arr_usd": sum_arr,
                "max_workstream_duration_months": float(max_ws_duration),
            },
            authoritative_source_citation=source_citation,
            findings=findings,
            verified_lineage_rows=lineage_rows,
        )

        if tool_context is not None:
            tool_context.state["gate1_blocking_count"] = len(blocking)
            existing_lineage = list(tool_context.state.get("numbers_lineage", []))
            existing_lineage.extend([row.model_dump() for row in lineage_rows])
            tool_context.state["numbers_lineage"] = existing_lineage

        logger.info(
            "gate1_reconciliation_completed",
            deal_id=req.deal_id,
            passed=report.passed,
            blocking_count=len(blocking),
        )
        return report.model_dump()
