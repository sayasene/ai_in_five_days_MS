"""Unit tests for strict Pydantic v2 JSON schemas and guided tool error recovery."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pursuit_auditor.schemas import (
    ExecutiveComprehensionBrief,
    GateFinding,
    NumbersLineageRow,
    PursuitAuditVerdict,
    StakeholderQAItem,
    export_tool_json_schemas,
)
from pursuit_auditor.tools import (
    audit_executive_aesthetic_and_density,
    certify_deliverable_for_external_release,
    fetch_authoritative_deal_baseline,
    inspect_commercial_posture_and_leak_guardrails,
    reconcile_financial_rollups_and_rate_cards,
    record_numbers_lineage_citation,
    request_human_waiver_signoff,
    retrieve_historical_audit_precedents,
)


def _sample_brief() -> ExecutiveComprehensionBrief:
    return ExecutiveComprehensionBrief(
        numbers_lineage=[
            NumbersLineageRow(
                metric="Total Cost to Migrate/Implement",
                value="$10,000,000",
                exact_source="Northstar_DealModel_v4_Final -> Post_Discount_InScope_Summary (Col G)",
                derivation="$3.2M GSF + $6.8M PSF",
            )
        ],
        key_deltas_and_assumptions=[
            "Aligned all workstreams to approved post-discount workbook tab.",
            "Control Tower governance sits at Row #1 with $0 direct ARR.",
            "Partner executes hands-on cutovers under stage-gate QA.",
        ],
        toughest_questions=[
            StakeholderQAItem(
                stakeholder_role=f"Stakeholder {i}",
                likely_question=f"Question {i}?",
                recommended_answer=f"Grounded answer {i}.",
            )
            for i in range(1, 6)
        ],
    )


def test_export_tool_json_schemas_contains_strict_contracts() -> None:
    schemas = export_tool_json_schemas()
    assert len(schemas) >= 15
    assert "FinancialReconciliationRequest" in schemas
    assert "ToolErrorResponse" in schemas
    assert schemas["FinancialReconciliationRequest"]["additionalProperties"] is False
    assert schemas["CommercialPostureAuditRequest"]["additionalProperties"] is False


def test_pursuit_audit_verdict_enforces_pass_revise_consistency() -> None:
    finding = GateFinding(
        finding_id="G1-01",
        location="Slide 2",
        gate="Gate 1",
        severity="BLOCKING",
        issue="Sum mismatch",
        exact_fix="Update summary total.",
    )
    with pytest.raises(ValidationError, match="REVISE"):
        PursuitAuditVerdict(
            verdict="PASS",
            bluf_summary="Invalid false pass.",
            blocking_fixes=[finding],
            executive_brief=_sample_brief(),
        )

    with pytest.raises(ValidationError, match="PASS"):
        PursuitAuditVerdict(
            verdict="REVISE",
            bluf_summary="Invalid revise without findings.",
            blocking_fixes=[],
            executive_brief=_sample_brief(),
        )

    valid = PursuitAuditVerdict(
        verdict="PASS",
        bluf_summary="Zero blocking defects across Gates 1-3.",
        blocking_fixes=[],
        executive_brief=_sample_brief(),
    )
    assert valid.verdict == "PASS"


def test_all_tools_return_guided_error_recovery_instead_of_crashing() -> None:
    # 1. Unknown deal baseline
    err1 = fetch_authoritative_deal_baseline(deal_id="OPP-DOES-NOT-EXIST")
    assert err1["status"] == "error"
    assert err1["error_code"] == "DEAL_BASELINE_NOT_FOUND"
    assert err1["recoverable"] is True
    assert len(err1["recovery_instructions"]) >= 2
    assert "OPP-SYNTH-2026-001" in err1["retry_example"]["deal_id"]

    # 2. Invalid Gate 1 financial inputs
    err2 = reconcile_financial_rollups_and_rate_cards(
        deal_id="OPP-SYNTH-2026-001",
        reported_summary_total_cost_usd=-50,
        reported_summary_gsf_usd=0,
        reported_summary_psf_usd=0,
        reported_summary_duration_months=0,
        reported_source_tab="x",
        workstreams=[],
    )
    assert err2["status"] == "error"
    assert err2["error_code"] == "SCHEMA_VALIDATION_ERROR"
    assert err2["missing_fields"]
    assert err2["recovery_instructions"]

    # 3. Invalid Gate 2 audience literal
    err3 = inspect_commercial_posture_and_leak_guardrails(
        deliverable_type="services_strategy_deck",
        audience="unknown_audience",
        full_text="short",
        table_headers=[],
        workstream_names_in_order=[],
        workstream_arr_map={},
        is_advisory_control_tower=True,
    )
    assert err3["status"] == "error"
    assert err3["error_code"] == "COMMERCIAL_AUDIT_SCHEMA_ERROR"

    # 4. Empty Gate 3 slides
    err4 = audit_executive_aesthetic_and_density(
        deliverable_type="services_strategy_deck",
        slides=[],
    )
    assert err4["status"] == "error"
    assert err4["error_code"] == "AESTHETIC_AUDIT_SCHEMA_ERROR"

    # 5. Invalid precedent search
    err5 = retrieve_historical_audit_precedents(query="a", gate_filter="Gate 99", top_k=50)
    assert err5["status"] == "error"
    assert err5["error_code"] == "PRECEDENT_QUERY_VALIDATION_ERROR"

    # 6. Invalid lineage citation
    err6 = record_numbers_lineage_citation(metric="", value="", exact_source="", derivation="")
    assert err6["status"] == "error"
    assert err6["error_code"] == "LINEAGE_CITATION_INVALID"

    # 7. Invalid HITL waiver justification
    err7 = request_human_waiver_signoff(
        deal_id="OPP-SYNTH-2026-001",
        finding_id="G1-01",
        waiver_justification="too short",
        approver_role="Intern",
    )
    assert err7["status"] == "error"
    assert err7["error_code"] == "WAIVER_REQUEST_INVALID"

    # 8. Invalid HITL release certification
    err8 = certify_deliverable_for_external_release(
        deal_id="x",
        deliverable_title="x",
        audience="bad",
        verdict="MAYBE",
        blocking_count=-1,
        total_deal_value_usd=-10,
    )
    assert err8["status"] == "error"
    assert err8["error_code"] == "CERTIFICATION_REQUEST_INVALID"
