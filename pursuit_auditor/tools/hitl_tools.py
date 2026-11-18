"""Human-in-the-Loop (HITL) confirmation hooks for high-stakes audit actions.

Implements explicit ADK code stops using `FunctionTool(..., require_confirmation=...)`
and `tool_context.request_confirmation(...)` so high-stakes governance decisions
(waiving a BLOCKING finding or certifying a customer/partner/high-TCV release)
cannot execute without explicit human approval.
"""

from __future__ import annotations

from typing import Any

from google.adk.tools import FunctionTool, ToolContext
from pydantic import ValidationError

from pursuit_auditor.config.settings import get_settings
from pursuit_auditor.observability.logging import get_logger
from pursuit_auditor.observability.tracing import traced_span
from pursuit_auditor.schemas import (
    DeliverableCertificationRequest,
    DeliverableCertificationResponse,
    HumanWaiverSignoffRequest,
    ToolErrorResponse,
)

logger = get_logger(__name__)


def requires_human_release_confirmation(
    deal_id: str,
    deliverable_title: str,
    audience: str,
    verdict: str,
    blocking_count: int,
    total_deal_value_usd: float,
    **_kwargs: Any,
) -> bool:
    """Conditional HITL predicate deciding whether external release certification must pause.

    Triggers an explicit human confirmation gate whenever ANY of these hold:
    - The deliverable is external-facing (`audience` is `"customer"` or `"partner"`).
    - One or more `BLOCKING` findings remain unresolved (`blocking_count > 0` or `verdict != "PASS"`).
    - Total deal value meets or exceeds the configured executive threshold ($5M default).
    """
    settings = get_settings()
    return (
        audience in ("customer", "partner")
        or blocking_count > 0
        or verdict != "PASS"
        or total_deal_value_usd >= settings.hitl_deal_value_threshold_usd
    )


def request_human_waiver_signoff(
    deal_id: str,
    finding_id: str,
    waiver_justification: str,
    approver_role: str,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Request explicit human sign-off to grant an exception waiver for a BLOCKING finding.

    Wrapped with `require_confirmation=True` in ADK so the runtime halts and
    prompts a human approver before recording any risk waiver.

    Args:
        deal_id: Synthetic opportunity identifier (e.g., `"OPP-SYNTH-2026-001"`).
        finding_id: Identifier of the BLOCKING finding being waived (e.g., `"G1-02"`).
        waiver_justification: Detailed business rationale (minimum 15 characters)
            explaining why the finding can be waived with controlled risk.
        approver_role: Required sign-off authority (`"Pursuit Lead"`,
            `"Practice Director"`, or `"Pricing Committee"`).

    Returns:
        Dictionary indicating whether the waiver was `"approved"`, `"rejected"`,
        or `"pending_human_confirmation"`, or `ToolErrorResponse` on invalid args.
    """
    with traced_span(
        "audit.tool.request_human_waiver_signoff",
        {"deal.id": deal_id, "finding.id": finding_id, "hitl.approver": approver_role},
    ):
        try:
            req = HumanWaiverSignoffRequest.model_validate(
                {
                    "deal_id": deal_id,
                    "finding_id": finding_id,
                    "waiver_justification": waiver_justification,
                    "approver_role": approver_role,
                }
            )
        except ValidationError as exc:
            return ToolErrorResponse(
                error_code="WAIVER_REQUEST_INVALID",
                recoverable=True,
                message="Waiver sign-off request failed schema validation.",
                missing_fields=[".".join(str(p) for p in e.get("loc", ())) for e in exc.errors()],
                recovery_instructions=[
                    "Provide a detailed `waiver_justification` (at least 15 characters).",
                    "Choose `approver_role` from: 'Pursuit Lead', 'Practice Director', 'Pricing Committee'.",
                ],
                retry_example={
                    "deal_id": "OPP-SYNTH-2026-001",
                    "finding_id": "G1-01",
                    "waiver_justification": "Pricing committee pre-approved temporary 2-week tab variance pending refresh.",
                    "approver_role": "Pricing Committee",
                },
            ).model_dump()

        if tool_context is not None:
            confirmation = getattr(tool_context, "tool_confirmation", None)
            if confirmation is None:
                tool_context.request_confirmation(
                    hint=(
                        f"HIGH-STAKES WAIVER APPROVAL REQUIRED ({req.approver_role}): "
                        f"Approve risk waiver for finding {req.finding_id} on {req.deal_id}? "
                        f"Rationale: {req.waiver_justification}"
                    ),
                    payload=req.model_dump(),
                )
                logger.warning(
                    "hitl_waiver_pause_requested",
                    deal_id=req.deal_id,
                    finding_id=req.finding_id,
                    approver_role=req.approver_role,
                )
                return {
                    "status": "pending_human_confirmation",
                    "deal_id": req.deal_id,
                    "finding_id": req.finding_id,
                    "approver_role": req.approver_role,
                    "message": "Execution paused awaiting explicit human confirmation.",
                }

            if not getattr(confirmation, "confirmed", False):
                logger.info(
                    "hitl_waiver_rejected",
                    deal_id=req.deal_id,
                    finding_id=req.finding_id,
                )
                return {
                    "status": "rejected",
                    "deal_id": req.deal_id,
                    "finding_id": req.finding_id,
                    "message": "Human reviewer rejected the waiver; finding remains BLOCKING.",
                }

            waivers = list(tool_context.state.get("approved_waivers", []))
            waivers.append(req.model_dump())
            tool_context.state["approved_waivers"] = waivers

        logger.info(
            "hitl_waiver_approved",
            deal_id=req.deal_id,
            finding_id=req.finding_id,
            approver_role=req.approver_role,
        )
        return {
            "status": "approved",
            "deal_id": req.deal_id,
            "finding_id": req.finding_id,
            "approver_role": req.approver_role,
            "human_confirmed": True,
            "message": "Human reviewer approved the risk waiver.",
        }


def certify_deliverable_for_external_release(
    deal_id: str,
    deliverable_title: str,
    audience: str,
    verdict: str,
    blocking_count: int,
    total_deal_value_usd: float,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Certify an audited deliverable for sharing after verifying HITL confirmation gates.

    Enforces an explicit ADK confirmation pause (`requires_human_release_confirmation`)
    for any customer/partner release, any unresolved `BLOCKING` finding, or any
    engagement >= $5M TCV. Never certifies a deliverable if a human rejects confirmation.

    Args:
        deal_id: Synthetic opportunity identifier.
        deliverable_title: Short human-readable title of the audited deliverable.
        audience: Intended audience (`"internal_leadership"`, `"pricing_committee"`,
            `"customer"`, or `"partner"`).
        verdict: Audit verdict (`"PASS"` or `"REVISE"`).
        blocking_count: Count of remaining `BLOCKING` findings across Gates 1-3.
        total_deal_value_usd: Total engagement contract value (TCV) in USD.

    Returns:
        A JSON-serializable `DeliverableCertificationResponse` (`certified`,
        `pending_human_confirmation`, or `rejected`), or `ToolErrorResponse`
        if input validation fails.
    """
    with traced_span(
        "audit.tool.certify_deliverable_for_external_release",
        {
            "deal.id": deal_id,
            "deliverable.audience": audience,
            "audit.blocking_count": blocking_count,
        },
    ):
        try:
            req = DeliverableCertificationRequest.model_validate(
                {
                    "deal_id": deal_id,
                    "deliverable_title": deliverable_title,
                    "audience": audience,
                    "verdict": verdict,
                    "blocking_count": blocking_count,
                    "total_deal_value_usd": total_deal_value_usd,
                }
            )
        except ValidationError as exc:
            return ToolErrorResponse(
                error_code="CERTIFICATION_REQUEST_INVALID",
                recoverable=True,
                message="Deliverable certification payload failed schema validation.",
                missing_fields=[".".join(str(p) for p in e.get("loc", ())) for e in exc.errors()],
                recovery_instructions=[
                    "Supply valid `audience`, `verdict` ('PASS' or 'REVISE'), non-negative `blocking_count`, and `total_deal_value_usd`.",
                ],
                retry_example={
                    "deal_id": "OPP-SYNTH-2026-001",
                    "deliverable_title": "Northstar Retail Services Strategy v4",
                    "audience": "internal_leadership",
                    "verdict": "PASS",
                    "blocking_count": 0,
                    "total_deal_value_usd": 45000000,
                },
            ).model_dump()

        needs_confirm = requires_human_release_confirmation(**req.model_dump())
        human_confirmed = False

        if needs_confirm and tool_context is not None:
            confirmation = getattr(tool_context, "tool_confirmation", None)
            if confirmation is None:
                tool_context.request_confirmation(
                    hint=(
                        f"HUMAN RELEASE GATE: Confirm releasing '{req.deliverable_title}' "
                        f"({req.deal_id}, TCV ${req.total_deal_value_usd:,.0f}) to "
                        f"audience='{req.audience}' with verdict={req.verdict} "
                        f"and blocking_count={req.blocking_count}."
                    ),
                    payload=req.model_dump(),
                )
                return DeliverableCertificationResponse(
                    status="pending_human_confirmation",
                    deal_id=req.deal_id,
                    deliverable_title=req.deliverable_title,
                    audience=req.audience,
                    human_confirmed=False,
                    certification_note=(
                        "Paused at Human-in-the-Loop release gate awaiting Pursuit Lead confirmation."
                    ),
                ).model_dump()

            if not getattr(confirmation, "confirmed", False):
                return DeliverableCertificationResponse(
                    status="rejected",
                    deal_id=req.deal_id,
                    deliverable_title=req.deliverable_title,
                    audience=req.audience,
                    human_confirmed=False,
                    certification_note="Release certification rejected by human reviewer.",
                ).model_dump()
            human_confirmed = True
        elif req.blocking_count > 0 or req.verdict != "PASS":
            return DeliverableCertificationResponse(
                status="rejected",
                deal_id=req.deal_id,
                deliverable_title=req.deliverable_title,
                audience=req.audience,
                human_confirmed=False,
                certification_note=(
                    "Cannot auto-certify a deliverable with unresolved BLOCKING findings without human approval."
                ),
            ).model_dump()

        response = DeliverableCertificationResponse(
            status="certified",
            deal_id=req.deal_id,
            deliverable_title=req.deliverable_title,
            audience=req.audience,
            human_confirmed=human_confirmed,
            certification_note=(
                "Deliverable certified for release after passing audit and human-in-the-loop controls."
                if human_confirmed
                else "Low-risk internal deliverable certified with 0 blocking issues."
            ),
        )
        if tool_context is not None:
            tool_context.state["release_certification"] = response.model_dump()
        logger.info(
            "deliverable_release_certified",
            deal_id=req.deal_id,
            audience=req.audience,
            human_confirmed=human_confirmed,
        )
        return response.model_dump()


# ADK FunctionTool wrappers with native HITL `require_confirmation` hooks
request_waiver_tool = FunctionTool(
    func=request_human_waiver_signoff,
    require_confirmation=True,
)

certify_deliverable_tool = FunctionTool(
    func=certify_deliverable_for_external_release,
    require_confirmation=requires_human_release_confirmation,
)
