"""Gate 2 tool: Commercial posture, audience confidentiality, and SOW guardrails."""

from __future__ import annotations

import re
from typing import Any

from google.adk.tools import ToolContext
from pydantic import ValidationError

from pursuit_auditor.observability.logging import get_logger
from pursuit_auditor.observability.tracing import traced_span
from pursuit_auditor.schemas import (
    CommercialPostureAuditRequest,
    CommercialPostureReport,
    GateFinding,
    ToolErrorResponse,
)

logger = get_logger(__name__)

_EXECUTION_OVERREACH_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "SI management overreach",
        re.compile(
            r"\b(?:manage|direct|supervise)\s+(?:the\s+)?(?:system integrator|si\b|partner delivery team|third-party vendor)",
            re.IGNORECASE,
        ),
    ),
    (
        "Hands-on execution claim",
        re.compile(
            r"\b(?:pso|cloud consulting)\s+(?:will\s+)?(?:execute|perform|run)\s+(?:all\s+)?(?:hands-on\s+)?(?:migration|cutover|etl|workload)\s+(?:execution|delivery|migration|cutovers)",
            re.IGNORECASE,
        ),
    ),
)

_EXTERNAL_PRICING_LEAK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "Internal FTE monthly rate card",
        re.compile(
            r"\$\s*\d{2,3}\s*[kK]\s*/\s*(?:mo|month)|rate\s*card|\d+\s*fte\s*[x×*]\s*\$\d+",
            re.IGNORECASE,
        ),
    ),
    (
        "Internal credit envelope or margin ratio",
        re.compile(
            r"\b(?:internal\s+margin|discount\s+ratio|credit\s+envelope|gsf[- ]to[- ]psf\s+ratio|offshore\s+rate\s+card|blended\s+rate\s+of\s+\$)\b",
            re.IGNORECASE,
        ),
    ),
)

_SOW_REQUIRED_CLAUSES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "Customer dependencies / responsibilities",
        re.compile(r"\bcustomer\s+(?:responsibilities|dependencies|prerequisites)\b", re.IGNORECASE),
    ),
    (
        "Objective acceptance criteria",
        re.compile(r"\bacceptance\s+(?:criteria|gates|sign-?off|sla)\b", re.IGNORECASE),
    ),
    (
        "Out-of-scope / boundary exclusions",
        re.compile(r"\b(?:out\s+of\s+scope|exclusions|scope\s+boundaries)\b", re.IGNORECASE),
    ),
)


def inspect_commercial_posture_and_leak_guardrails(
    deliverable_type: str,
    audience: str,
    full_text: str,
    table_headers: list[str],
    workstream_names_in_order: list[str],
    workstream_arr_map: dict[str, float],
    is_advisory_control_tower: bool,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Run Gate 2 audit for advisory boundaries, external pricing leaks, and strategy conventions.

    Enforces:
    1. Advisory vs. execution accountability boundaries (blocks hands-on SI
       management or execution claims under a Control Tower construct).
    2. Audience confidentiality (blocks internal rate cards, credit envelopes,
       or FTE-to-dollar pricing math in `customer` or `partner` deliverables).
    3. Services Strategy table conventions: Program Governance (`DE & EA`) at
       rank #1 (`$0` direct ARR), Infrastructure Migration broken out from
       Data/App Migration, split `GSF (PSO)` & `PSF (Partner)` columns, and
       `Total cost to migrate/implement` header.
    4. SOW / Preview SOW mandatory guardrails (customer dependencies, objective
       acceptance criteria, explicit scope exclusions).

    Args:
        deliverable_type: Deliverable category (`"services_strategy_deck"`,
            `"executive_whitepaper"`, `"customer_proposal"`, `"partner_proposal"`,
            `"preview_sow"`, or `"sow"`).
        audience: Target reader (`"internal_leadership"`, `"pricing_committee"`,
            `"customer"`, or `"partner"`).
        full_text: Full extracted text of slides or document sections to inspect.
        table_headers: Exact column header labels from the funding/credit summary table.
        workstream_names_in_order: Workstream titles in display order (index 0 = rank #1).
        workstream_arr_map: Mapping of workstream title to reported steady-state ARR in USD.
        is_advisory_control_tower: True when consulting posture is Control Tower / advisory.

    Returns:
        A JSON-serializable `CommercialPostureReport` dictionary (`passed`,
        `external_leak_detected`, `findings`), or `ToolErrorResponse` with
        actionable recovery instructions if validation fails.
    """
    with traced_span(
        "audit.tool.inspect_commercial_posture_and_leak_guardrails",
        {"deliverable.type": deliverable_type, "deliverable.audience": audience},
    ):
        try:
            req = CommercialPostureAuditRequest.model_validate(
                {
                    "deliverable_type": deliverable_type,
                    "audience": audience,
                    "full_text": full_text,
                    "table_headers": table_headers,
                    "workstream_names_in_order": workstream_names_in_order,
                    "workstream_arr_map": workstream_arr_map,
                    "is_advisory_control_tower": is_advisory_control_tower,
                }
            )
        except ValidationError as exc:
            return ToolErrorResponse(
                error_code="COMMERCIAL_AUDIT_SCHEMA_ERROR",
                recoverable=True,
                message=f"Commercial posture audit input failed validation ({len(exc.errors())} issue(s)).",
                missing_fields=[".".join(str(p) for p in e.get("loc", ())) for e in exc.errors()],
                recovery_instructions=[
                    "Use one of the allowed `deliverable_type` and `audience` literals.",
                    "Provide at least 10 characters in `full_text` and pass lists/dicts for table metadata.",
                ],
                retry_example={
                    "deliverable_type": "services_strategy_deck",
                    "audience": "internal_leadership",
                    "full_text": "Program Governance (DE & EA) serves as Control Tower...",
                    "table_headers": [
                        "Workstream",
                        "Steady-State ARR",
                        "GSF (PSO)",
                        "PSF (Partner)",
                        "Total cost to migrate/implement",
                    ],
                    "workstream_names_in_order": [
                        "Program Governance (DE & EA)",
                        "Infrastructure Migration",
                    ],
                    "workstream_arr_map": {"Program Governance (DE & EA)": 0},
                    "is_advisory_control_tower": True,
                },
            ).model_dump()

        findings: list[GateFinding] = []
        seq = 1
        external_leak_detected = False

        def add_finding(location: str, issue: str, exact_fix: str, severity: str = "BLOCKING") -> None:
            nonlocal seq
            findings.append(
                GateFinding(
                    finding_id=f"G2-{seq:02d}",
                    location=location,
                    gate="Gate 2",
                    severity="BLOCKING" if severity == "BLOCKING" else "POLISH",
                    issue=issue,
                    exact_fix=exact_fix,
                )
            )
            seq += 1

        # 1. Advisory vs. execution posture boundaries
        if req.is_advisory_control_tower:
            for label, pattern in _EXECUTION_OVERREACH_PATTERNS:
                match = pattern.search(req.full_text)
                if match:
                    add_finding(
                        location="Delivery Posture / Scope Narrative",
                        issue=(
                            f"Detected {label} phrase ('{match.group(0)}') that implies hands-on "
                            "execution or SI management under a Control Tower advisory construct."
                        ),
                        exact_fix=(
                            "Reframe consulting scope around architecture stage-gate QA, golden "
                            "blueprints, and AI migration accelerators; assign hands-on cutover "
                            "and SI team management explicitly to the SI partner and customer."
                        ),
                    )

        # 2. Audience confidentiality & pricing-leak guardrails
        if req.audience in ("customer", "partner"):
            for label, pattern in _EXTERNAL_PRICING_LEAK_PATTERNS:
                match = pattern.search(req.full_text)
                if match:
                    external_leak_detected = True
                    add_finding(
                        location=f"External Confidentiality ({req.audience.title()} Audience)",
                        issue=(
                            f"External-facing deliverable exposes {label} ('{match.group(0)}'), "
                            "allowing reverse-engineering of internal consulting pricing."
                        ),
                        exact_fix=(
                            "Strip internal monthly FTE rate cards, credit ratios, and margin/discount "
                            "figures; present deliverable-based milestones and high-level commercial totals only."
                        ),
                    )

        # 3. Services Strategy conventions (internal strategy decks)
        if req.deliverable_type == "services_strategy_deck":
            if req.workstream_names_in_order:
                top_ws = req.workstream_names_in_order[0].lower()
                if "governance" not in top_ws and "control tower" not in top_ws:
                    add_finding(
                        location="Workstream Table — Row #1",
                        issue=(
                            f"Row #1 is '{req.workstream_names_in_order[0]}' rather than "
                            "Program Governance (DE & EA) positioned as Control Tower."
                        ),
                        exact_fix=(
                            "Move 'Program Governance (DE & EA)' to Row #1 as Control Tower "
                            "with $0 direct steady-state ARR."
                        ),
                    )
                for ws_name, arr_val in req.workstream_arr_map.items():
                    if ("governance" in ws_name.lower() or "control tower" in ws_name.lower()) and arr_val > 0:
                        add_finding(
                            location=f"Workstream Table — {ws_name}",
                            issue=(
                                f"Non-ARR enabling workstream '{ws_name}' shows ${arr_val:,.0f} ARR "
                                "instead of $0."
                            ),
                            exact_fix=f"Set '{ws_name}' direct steady-state ARR to $0.",
                        )

                combined_infra = [
                    name
                    for name in req.workstream_names_in_order
                    if "infra" in name.lower()
                    and ("data" in name.lower() or "app" in name.lower())
                ]
                if combined_infra:
                    add_finding(
                        location=f"Workstream Structure — {combined_infra[0]}",
                        issue=(
                            f"Infrastructure Migration is lumped into '{combined_infra[0]}', "
                            "distorting clean PSO-to-Partner workstream ratios."
                        ),
                        exact_fix=(
                            "Break out 'Infrastructure Migration' as its own dedicated row separate "
                            "from Data Platform and Application Modernization."
                        ),
                    )

            if req.table_headers:
                headers_lower = [h.strip().lower() for h in req.table_headers]
                if not any("total cost to migrate/implement" in h for h in headers_lower):
                    add_finding(
                        location="Credit Support Table Headers",
                        issue="Missing required column header 'Total cost to migrate/implement'.",
                        exact_fix="Rename the total funding column to 'Total cost to migrate/implement'.",
                    )
                has_gsf = any("gsf" in h for h in headers_lower)
                has_psf = any("psf" in h for h in headers_lower)
                if not (has_gsf and has_psf):
                    add_finding(
                        location="Credit Support Table Headers",
                        issue="Funding table must split 'GSF (PSO)' and 'PSF (Partner)' into separate columns.",
                        exact_fix="Split combined funding column into separate 'GSF (PSO)' and 'PSF (Partner)' columns where GSF + PSF = Total.",
                    )

        # 4. SOW / Preview SOW mandatory clauses
        if req.deliverable_type in ("sow", "preview_sow"):
            for clause_name, pattern in _SOW_REQUIRED_CLAUSES:
                if not pattern.search(req.full_text):
                    add_finding(
                        location=f"{req.deliverable_type.upper()} Guardrail Clauses",
                        issue=f"Missing mandatory SOW section covering {clause_name}.",
                        exact_fix=f"Add an explicit '{clause_name}' subsection with measurable timelines and owner accountability.",
                    )

        blocking = [f for f in findings if f.severity == "BLOCKING"]
        report = CommercialPostureReport(
            audience=req.audience,
            deliverable_type=req.deliverable_type,
            passed=len(blocking) == 0,
            external_leak_detected=external_leak_detected,
            findings=findings,
        )

        if tool_context is not None:
            tool_context.state["gate2_blocking_count"] = len(blocking)
            tool_context.state["external_leak_detected"] = external_leak_detected

        logger.info(
            "gate2_commercial_audit_completed",
            deliverable_type=req.deliverable_type,
            audience=req.audience,
            passed=report.passed,
            external_leak_detected=external_leak_detected,
            blocking_count=len(blocking),
        )
        return report.model_dump()
