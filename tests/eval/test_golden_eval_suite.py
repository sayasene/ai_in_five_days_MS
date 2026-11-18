"""Automated golden evaluation regression harness (validates ADK EvalSet + 6 scenarios).

Runs deterministically in CI without live LLM flakiness AND verifies that the
ADK `.evalset.json` and `eval_config.json` strictly conform to `google-adk`
evaluation schemas for live `adk eval` / `agents-cli eval run` execution.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from google.adk.evaluation.eval_set import EvalSet

from pursuit_auditor.tools import (
    audit_executive_aesthetic_and_density,
    certify_deliverable_for_external_release,
    fetch_authoritative_deal_baseline,
    inspect_commercial_posture_and_leak_guardrails,
    reconcile_financial_rollups_and_rate_cards,
)
from tests.conftest import FakeToolContext

EVAL_DIR = Path(__file__).resolve().parent
EVALSET_PATH = EVAL_DIR / "evalsets" / "pursuit_auditor_golden.evalset.json"
EVAL_CONFIG_PATH = EVAL_DIR / "eval_config.json"


def test_adk_evalset_and_eval_config_validate_cleanly() -> None:
    """Confirm golden dataset strictly parses against `google.adk.evaluation.eval_set.EvalSet`."""
    raw_evalset = json.loads(EVALSET_PATH.read_text(encoding="utf-8"))
    eval_set = EvalSet.model_validate(raw_evalset)
    assert eval_set.eval_set_id == "pursuit_auditor_golden_v1"
    assert len(eval_set.eval_cases) == 6
    assert len({c.eval_id for c in eval_set.eval_cases}) == 6

    raw_cfg = json.loads(EVAL_CONFIG_PATH.read_text(encoding="utf-8"))
    assert raw_cfg["criteria"]["safety_v1"] == 1.0
    assert raw_cfg["criteria"]["tool_trajectory_avg_score"]["threshold"] >= 0.85


@dataclass(frozen=True)
class GoldenScenario:
    case_id: str
    deal_id: str
    deliverable_type: str
    audience: str
    summary_total: float
    summary_gsf: float
    summary_psf: float
    summary_months: int
    source_tab: str
    full_text: str
    table_headers: list[str]
    slides: list[dict[str, Any]]
    expected_verdict: str
    expected_failed_gates: set[str]
    expect_hitl_pause: bool = False
    mutate_workstream_months: int | None = None


def _build_golden_scenarios() -> list[GoldenScenario]:
    clean_headers = [
        "Workstream",
        "Steady-State ARR",
        "GSF (PSO)",
        "PSF (Partner)",
        "Total cost to migrate/implement",
    ]
    clean_slide = {
        "slide_number": 1,
        "title": "Control Tower drives 18-month cloud modernization",
        "has_top_left_kicker": True,
        "has_title_divider_line": False,
        "uses_rounded_corners": False,
        "hex_colors_used": ["#FFFFFF", "#202124", "#5F6368", "#1A73E8"],
        "font_sizes_pt": [10.0, 12.0, 18.0],
        "table_cells": ["Deliver 15 golden GCP landing zone patterns"],
        "body_text": "Stage-gate architecture QA and AI migration accelerators.",
    }
    return [
        GoldenScenario(
            case_id="case_01_clean_northstar_pass",
            deal_id="OPP-SYNTH-2026-001",
            deliverable_type="services_strategy_deck",
            audience="internal_leadership",
            summary_total=10_000_000,
            summary_gsf=3_200_000,
            summary_psf=6_800_000,
            summary_months=18,
            source_tab="Post_Discount_InScope_Summary",
            full_text="Program Governance (DE & EA) operates as Control Tower with stage-gate QA.",
            table_headers=clean_headers,
            slides=[clean_slide],
            expected_verdict="PASS",
            expected_failed_gates=set(),
        ),
        GoldenScenario(
            case_id="case_02_gate1_math_and_raw_tab_drift_revise",
            deal_id="OPP-SYNTH-2026-001",
            deliverable_type="services_strategy_deck",
            audience="pricing_committee",
            summary_total=12_000_000,
            summary_gsf=4_000_000,
            summary_psf=8_000_000,
            summary_months=18,
            source_tab="Legacy_Raw_Tab",
            full_text="Program Governance (DE & EA) Control Tower summary.",
            table_headers=clean_headers,
            slides=[clean_slide],
            expected_verdict="REVISE",
            expected_failed_gates={"Gate 1"},
            mutate_workstream_months=24,
        ),
        GoldenScenario(
            case_id="case_03_gate2_partner_rate_card_leak_revise",
            deal_id="OPP-SYNTH-2026-002",
            deliverable_type="partner_proposal",
            audience="partner",
            summary_total=5_400_000,
            summary_gsf=1_800_000,
            summary_psf=3_600_000,
            summary_months=12,
            source_tab="Approved_Funding_Matrix",
            full_text="Partner joint scope. Internal rate card is $150K/mo with GSF-to-PSF ratio.",
            table_headers=clean_headers,
            slides=[clean_slide],
            expected_verdict="REVISE",
            expected_failed_gates={"Gate 2"},
        ),
        GoldenScenario(
            case_id="case_04_gate2_control_tower_execution_overreach_revise",
            deal_id="OPP-SYNTH-2026-003",
            deliverable_type="preview_sow",
            audience="customer",
            summary_total=1_950_000,
            summary_gsf=950_000,
            summary_psf=1_000_000,
            summary_months=6,
            source_tab="SOW_Milestone_Schedule",
            full_text="Cloud Consulting will manage the system integrator delivery team and run all hands-on ETL cutovers.",
            table_headers=clean_headers,
            slides=[clean_slide],
            expected_verdict="REVISE",
            expected_failed_gates={"Gate 2"},
        ),
        GoldenScenario(
            case_id="case_05_gate3_aesthetic_rainbow_and_wall_of_text_revise",
            deal_id="OPP-SYNTH-2026-001",
            deliverable_type="services_strategy_deck",
            audience="internal_leadership",
            summary_total=10_000_000,
            summary_gsf=3_200_000,
            summary_psf=6_800_000,
            summary_months=18,
            source_tab="Post_Discount_InScope_Summary",
            full_text="Control Tower architecture QA and golden blueprints.",
            table_headers=clean_headers,
            slides=[
                {
                    "slide_number": 3,
                    "title": "🔥 Migration Funding",
                    "has_top_left_kicker": True,
                    "has_title_divider_line": True,
                    "uses_rounded_corners": True,
                    "hex_colors_used": ["#FFFFFF", "#EA4335", "#FBBC05", "#E8F0FE"],
                    "font_sizes_pt": [8.0, 12.0],
                    "table_cells": [
                        "This cell has way too many words and creates a cluttered wall of text exceeding fifteen words."
                    ],
                    "body_text": "",
                }
            ],
            expected_verdict="REVISE",
            expected_failed_gates={"Gate 3"},
        ),
        GoldenScenario(
            case_id="case_06_hitl_high_tcv_release_confirmation_pause",
            deal_id="OPP-SYNTH-2026-001",
            deliverable_type="customer_proposal",
            audience="customer",
            summary_total=10_000_000,
            summary_gsf=3_200_000,
            summary_psf=6_800_000,
            summary_months=18,
            source_tab="Post_Discount_InScope_Summary",
            full_text="Outcome-focused executive proposal with joint milestones and stage-gate quality assurance.",
            table_headers=clean_headers,
            slides=[clean_slide],
            expected_verdict="PASS",
            expected_failed_gates=set(),
            expect_hitl_pause=True,
        ),
    ]


@pytest.mark.parametrize("scenario", _build_golden_scenarios(), ids=lambda s: s.case_id)
def test_golden_regression_scenario(scenario: GoldenScenario) -> None:
    """Measure deterministic 4-gate regression accuracy against each golden benchmark case."""
    baseline = fetch_authoritative_deal_baseline(scenario.deal_id)
    workstreams = [dict(ws) for ws in baseline["workstreams"]]
    if scenario.mutate_workstream_months is not None:
        workstreams[-1]["duration_months"] = scenario.mutate_workstream_months

    g1 = reconcile_financial_rollups_and_rate_cards(
        deal_id=scenario.deal_id,
        reported_summary_total_cost_usd=scenario.summary_total,
        reported_summary_gsf_usd=scenario.summary_gsf,
        reported_summary_psf_usd=scenario.summary_psf,
        reported_summary_duration_months=scenario.summary_months,
        reported_source_tab=scenario.source_tab,
        workstreams=workstreams,
    )
    g2 = inspect_commercial_posture_and_leak_guardrails(
        deliverable_type=scenario.deliverable_type,
        audience=scenario.audience,
        full_text=scenario.full_text,
        table_headers=scenario.table_headers,
        workstream_names_in_order=[w["name"] for w in workstreams],
        workstream_arr_map={w["name"]: w["steady_state_arr_usd"] for w in workstreams},
        is_advisory_control_tower=True,
    )
    g3 = audit_executive_aesthetic_and_density(
        deliverable_type=scenario.deliverable_type,
        slides=scenario.slides,
    )

    failed_gates: set[str] = set()
    if not g1["passed"]:
        failed_gates.add("Gate 1")
    if not g2["passed"]:
        failed_gates.add("Gate 2")
    if not g3["passed"]:
        failed_gates.add("Gate 3")

    actual_verdict = "REVISE" if failed_gates else "PASS"
    assert actual_verdict == scenario.expected_verdict
    assert failed_gates == scenario.expected_failed_gates

    if scenario.expect_hitl_pause:
        ctx = FakeToolContext(tool_confirmation=None)
        cert = certify_deliverable_for_external_release(
            deal_id=scenario.deal_id,
            deliverable_title=f"{baseline['account_name']} Proposal",
            audience=scenario.audience,
            verdict=actual_verdict,
            blocking_count=0,
            total_deal_value_usd=baseline["total_tcv_usd"],
            tool_context=ctx,  # type: ignore[arg-type]
        )
        assert cert["status"] == "pending_human_confirmation"
        assert len(ctx.requested_confirmations) == 1
