"""Unit tests for Human-in-the-Loop (HITL) confirmation hooks and pause/resume flows."""

from __future__ import annotations

from pursuit_auditor.tools import (
    certify_deliverable_for_external_release,
    certify_deliverable_tool,
    request_human_waiver_signoff,
    request_waiver_tool,
    requires_human_release_confirmation,
)
from tests.conftest import FakeToolConfirmation, FakeToolContext


def test_adk_function_tools_have_confirmation_hooks_configured() -> None:
    assert request_waiver_tool._require_confirmation is True
    assert callable(certify_deliverable_tool._require_confirmation)


def test_release_confirmation_predicate_triggers_on_high_stakes_conditions() -> None:
    # Low-risk internal PASS below $5M threshold -> no pause required
    assert (
        requires_human_release_confirmation(
            deal_id="OPP-SYNTH-2026-003",
            deliverable_title="Internal Draft",
            audience="internal_leadership",
            verdict="PASS",
            blocking_count=0,
            total_deal_value_usd=2_000_000,
        )
        is False
    )
    # External customer audience -> MUST pause
    assert (
        requires_human_release_confirmation(
            deal_id="OPP-SYNTH-2026-003",
            deliverable_title="Customer Proposal",
            audience="customer",
            verdict="PASS",
            blocking_count=0,
            total_deal_value_usd=1_000_000,
        )
        is True
    )
    # High TCV >= $5M -> MUST pause even for internal leadership
    assert (
        requires_human_release_confirmation(
            deal_id="OPP-SYNTH-2026-001",
            deliverable_title="Northstar Strategy",
            audience="internal_leadership",
            verdict="PASS",
            blocking_count=0,
            total_deal_value_usd=45_000_000,
        )
        is True
    )


def test_waiver_signoff_pauses_then_handles_rejection_and_approval() -> None:
    # Step 1: First invocation without human confirmation triggers explicit pause
    ctx_pause = FakeToolContext(tool_confirmation=None)
    paused = request_human_waiver_signoff(
        deal_id="OPP-SYNTH-2026-001",
        finding_id="G1-02",
        waiver_justification="Pricing leadership pre-approved transitional tab pending Friday refresh.",
        approver_role="Pricing Committee",
        tool_context=ctx_pause,  # type: ignore[arg-type]
    )
    assert paused["status"] == "pending_human_confirmation"
    assert len(ctx_pause.requested_confirmations) == 1
    assert "HIGH-STAKES WAIVER APPROVAL REQUIRED" in ctx_pause.requested_confirmations[0]["hint"]

    # Step 2: Rejection path keeps finding BLOCKING
    ctx_reject = FakeToolContext(tool_confirmation=FakeToolConfirmation(confirmed=False))
    rejected = request_human_waiver_signoff(
        deal_id="OPP-SYNTH-2026-001",
        finding_id="G1-02",
        waiver_justification="Pricing leadership pre-approved transitional tab pending Friday refresh.",
        approver_role="Pricing Committee",
        tool_context=ctx_reject,  # type: ignore[arg-type]
    )
    assert rejected["status"] == "rejected"

    # Step 3: Explicit human approval records waiver in persistent session state
    ctx_approve = FakeToolContext(tool_confirmation=FakeToolConfirmation(confirmed=True))
    approved = request_human_waiver_signoff(
        deal_id="OPP-SYNTH-2026-001",
        finding_id="G1-02",
        waiver_justification="Pricing leadership pre-approved transitional tab pending Friday refresh.",
        approver_role="Pricing Committee",
        tool_context=ctx_approve,  # type: ignore[arg-type]
    )
    assert approved["status"] == "approved"
    assert approved["human_confirmed"] is True
    assert len(ctx_approve.state["approved_waivers"]) == 1


def test_deliverable_certification_enforces_hitl_pause_and_approval() -> None:
    ctx_pause = FakeToolContext(tool_confirmation=None)
    paused = certify_deliverable_for_external_release(
        deal_id="OPP-SYNTH-2026-001",
        deliverable_title="Northstar Retail Customer Proposal",
        audience="customer",
        verdict="PASS",
        blocking_count=0,
        total_deal_value_usd=45_000_000,
        tool_context=ctx_pause,  # type: ignore[arg-type]
    )
    assert paused["status"] == "pending_human_confirmation"
    assert len(ctx_pause.requested_confirmations) == 1

    ctx_ok = FakeToolContext(tool_confirmation=FakeToolConfirmation(confirmed=True))
    certified = certify_deliverable_for_external_release(
        deal_id="OPP-SYNTH-2026-001",
        deliverable_title="Northstar Retail Customer Proposal",
        audience="customer",
        verdict="PASS",
        blocking_count=0,
        total_deal_value_usd=45_000_000,
        tool_context=ctx_ok,  # type: ignore[arg-type]
    )
    assert certified["status"] == "certified"
    assert certified["human_confirmed"] is True
