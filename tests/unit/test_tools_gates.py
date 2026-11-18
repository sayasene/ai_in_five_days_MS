"""Unit tests for Gate 1, Gate 2, Gate 3, and Precedent/Lineage tools."""

from __future__ import annotations

from pursuit_auditor.tools import (
    audit_executive_aesthetic_and_density,
    fetch_authoritative_deal_baseline,
    inspect_commercial_posture_and_leak_guardrails,
    reconcile_financial_rollups_and_rate_cards,
    record_numbers_lineage_citation,
    retrieve_historical_audit_precedents,
)
from tests.conftest import FakeToolContext


def _northstar_clean_workstreams() -> list[dict]:
    baseline = fetch_authoritative_deal_baseline("OPP-SYNTH-2026-001")
    return baseline["workstreams"]


def test_gate1_reconciliation_passes_clean_northstar_construct() -> None:
    ctx = FakeToolContext()
    baseline = fetch_authoritative_deal_baseline("OPP-SYNTH-2026-001", tool_context=ctx)
    assert baseline["status"] == "success"
    assert ctx.state["active_account_name"] == "Northstar Retail"

    report = reconcile_financial_rollups_and_rate_cards(
        deal_id="OPP-SYNTH-2026-001",
        reported_summary_total_cost_usd=10_000_000,
        reported_summary_gsf_usd=3_200_000,
        reported_summary_psf_usd=6_800_000,
        reported_summary_duration_months=18,
        reported_source_tab="Post_Discount_InScope_Summary",
        workstreams=_northstar_clean_workstreams(),
        tool_context=ctx,
    )
    assert report["status"] == "success"
    assert report["passed"] is True
    assert report["findings"] == []
    assert len(report["verified_lineage_rows"]) == 4
    assert ctx.state["gate1_blocking_count"] == 0


def test_gate1_reconciliation_detects_tab_rollup_rate_and_timeline_drift() -> None:
    bad_workstreams = _northstar_clean_workstreams()
    # Introduce row sum error, rate-card drift, and 24-month chevron mismatch
    bad_workstreams[0]["total_cost_usd"] = 1_500_000  # GSF 1.35M + PSF 0 != 1.5M
    bad_workstreams[0]["planned_fte_months"] = 2.0  # 2 x $150K = $300K vs $1.35M ask
    bad_workstreams[2]["duration_months"] = 24  # Summary says 18

    report = reconcile_financial_rollups_and_rate_cards(
        deal_id="OPP-SYNTH-2026-001",
        reported_summary_total_cost_usd=12_000_000,
        reported_summary_gsf_usd=4_000_000,
        reported_summary_psf_usd=8_000_000,
        reported_summary_duration_months=18,
        reported_source_tab="Legacy_Raw_Unfiltered_Tab",
        workstreams=bad_workstreams,
    )
    assert report["passed"] is False
    issues = " ".join(f["issue"] for f in report["findings"])
    assert "Legacy_Raw_Unfiltered_Tab" in issues
    assert "benchmark math" in issues
    assert "24 months" in issues
    assert len(report["findings"]) >= 5


def test_gate2_detects_external_pricing_leak_and_execution_overreach() -> None:
    ctx = FakeToolContext()
    report = inspect_commercial_posture_and_leak_guardrails(
        deliverable_type="partner_proposal",
        audience="partner",
        full_text=(
            "Cloud Consulting will manage the system integrator delivery team and "
            "execute all hands-on migration cutovers. Internal rate card is $150K/mo "
            "with a 32% internal margin and GSF-to-PSF ratio."
        ),
        table_headers=["Workstream", "Total Funding"],
        workstream_names_in_order=["Data & Infrastructure Migration"],
        workstream_arr_map={"Data & Infrastructure Migration": 4_000_000},
        is_advisory_control_tower=True,
        tool_context=ctx,
    )
    assert report["passed"] is False
    assert report["external_leak_detected"] is True
    assert ctx.state["external_leak_detected"] is True
    assert len(report["findings"]) >= 3


def test_gate2_sow_checks_mandatory_clauses() -> None:
    missing = inspect_commercial_posture_and_leak_guardrails(
        deliverable_type="preview_sow",
        audience="customer",
        full_text="Phase 1 builds an agentic clinical platform over six months with joint checkpoints.",
        table_headers=[],
        workstream_names_in_order=[],
        workstream_arr_map={},
        is_advisory_control_tower=True,
    )
    assert missing["passed"] is False
    assert len(missing["findings"]) == 3

    compliant = inspect_commercial_posture_and_leak_guardrails(
        deliverable_type="preview_sow",
        audience="customer",
        full_text=(
            "Phase 1 defines Customer Dependencies, explicit Acceptance Criteria with a "
            "5-day sign-off SLA, and Out of Scope exclusions for legacy ERP refactoring."
        ),
        table_headers=[],
        workstream_names_in_order=[],
        workstream_arr_map={},
        is_advisory_control_tower=True,
    )
    assert compliant["passed"] is True


def test_gate3_detects_rainbow_palette_rounded_cards_wall_of_text_and_emojis() -> None:
    report = audit_executive_aesthetic_and_density(
        deliverable_type="services_strategy_deck",
        slides=[
            {
                "slide_number": 1,
                "title": "🚀 Executive Overview",
                "has_top_left_kicker": True,
                "has_title_divider_line": True,
                "uses_rounded_corners": True,
                "hex_colors_used": ["#FFFFFF", "#4285F4", "#E8F0FE"],
                "font_sizes_pt": [8.0, 11.0, 28.0],
                "table_cells": [
                    "This table cell is way too long and acts as a dense wall of text exceeding fifteen words by a wide margin."
                ],
                "body_text": "Provides general advisory support across all phases.",
            },
            {
                "slide_number": 2,
                "title": "Clean Financial Summary",
                "has_top_left_kicker": False,  # Breaks all-or-nothing kicker rule
                "has_title_divider_line": False,
                "uses_rounded_corners": False,
                "hex_colors_used": ["#FFFFFF", "#202124", "#1A73E8"],
                "font_sizes_pt": [10.0, 12.0, 18.0],
                "table_cells": ["Deliver 15 golden cloud landing zone patterns"],
                "body_text": "",
            },
        ],
    )
    assert report["passed"] is False
    assert report["blocking_count"] >= 6
    assert report["polish_count"] >= 2


def test_precedent_retrieval_and_lineage_recording() -> None:
    ctx = FakeToolContext()
    res = retrieve_historical_audit_precedents(
        query="internal rate card leak in partner deck",
        gate_filter="Gate 2",
        top_k=2,
        tool_context=ctx,
    )
    assert res["status"] == "success"
    assert res["matches"]
    assert res["matches"][0]["precedent_id"] == "PREC-2026-002"

    rec = record_numbers_lineage_citation(
        metric="Total TCV",
        value="$45,000,000",
        exact_source="Northstar_DealModel_v4_Final -> Post_Discount_InScope_Summary (Col G)",
        derivation="3-year contracted cloud commitment",
        tool_context=ctx,
    )
    assert rec["status"] == "recorded"
    assert len(ctx.state["numbers_lineage"]) == 1
