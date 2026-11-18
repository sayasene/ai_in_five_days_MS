"""Strict Pydantic v2 JSON schemas for all Pursuit Auditor tools, gates, and briefs.

Every tool validates inputs and outputs against explicit schemas with
`extra="forbid"` to constrain LLM tool-calling arguments and emit actionable
guided error payloads when constraints are violated.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AudienceType = Literal["internal_leadership", "pricing_committee", "customer", "partner"]
DeliverableType = Literal[
    "services_strategy_deck",
    "executive_whitepaper",
    "customer_proposal",
    "partner_proposal",
    "preview_sow",
    "sow",
]
SeverityLevel = Literal["BLOCKING", "POLISH"]
GateName = Literal["Gate 1", "Gate 2", "Gate 3", "Gate 4"]
RateTier = Literal[
    "governance_senior_fte",
    "onshore_specialist_fte",
    "offshore_specialist_fte",
    "blended_50_50_fte",
]


# ---------------------------------------------------------------------------
# Guided Error Recovery Schema (Rubric 1.4)
# ---------------------------------------------------------------------------


class ToolErrorResponse(BaseModel):
    """Structured error contract returning concrete recovery guidance to the LLM."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["error"] = "error"
    error_code: str = Field(description="Machine-readable error identifier.")
    recoverable: bool = Field(
        default=True,
        description="Whether the LLM can recover by adjusting arguments or fetching missing context.",
    )
    message: str = Field(description="Concise explanation of what failed.")
    missing_fields: list[str] = Field(
        default_factory=list,
        description="List of missing or invalid fields that must be supplied.",
    )
    recovery_instructions: list[str] = Field(
        description="Step-by-step instructions telling the LLM how to correct and retry."
    )
    retry_example: dict[str, Any] | None = Field(
        default=None,
        description="Minimal valid example payload illustrating the expected schema.",
    )


# ---------------------------------------------------------------------------
# Core Audit Finding & Gate Output Schemas
# ---------------------------------------------------------------------------


class GateFinding(BaseModel):
    """Single actionable finding produced by one of the four audit gates."""

    model_config = ConfigDict(extra="forbid")

    finding_id: str = Field(description="Unique identifier such as G1-01 or G2-03.")
    location: str = Field(description="Slide number, table name, or document section.")
    gate: GateName = Field(description="Audit gate that raised the finding.")
    severity: SeverityLevel = Field(description="BLOCKING prevents release; POLISH is optional.")
    issue: str = Field(description="Factual description of the discrepancy or violation.")
    exact_fix: str = Field(description="Concrete, copy-ready fix instruction for the author.")


class NumbersLineageRow(BaseModel):
    """Maps a single headline metric back to its authoritative source and formula."""

    model_config = ConfigDict(extra="forbid")

    metric: str = Field(description="Name of the financial or volumetric metric.")
    value: str = Field(description="Formatted metric value as shown in the deliverable.")
    exact_source: str = Field(description="Authoritative workbook, tab, and column citation.")
    derivation: str = Field(description="Formula or arithmetic basis for the value.")


class StakeholderQAItem(BaseModel):
    """High-stakes stakeholder question paired with a fact-grounded answer."""

    model_config = ConfigDict(extra="forbid")

    stakeholder_role: str = Field(
        description="Target persona asking the question (e.g., Pricing Lead, Customer CIO, SI Partner)."
    )
    likely_question: str = Field(description="Tough, realistic question expected in live review.")
    recommended_answer: str = Field(
        description="Crisp 2-3 sentence answer grounded strictly in verified deliverable facts."
    )


class ExecutiveComprehensionBrief(BaseModel):
    """Mandatory 1-page Executive Comprehension & Q&A Brief (Gate 4)."""

    model_config = ConfigDict(extra="forbid")

    numbers_lineage: list[NumbersLineageRow] = Field(
        min_length=1,
        description="Source-of-truth lineage table mapping every headline figure.",
    )
    key_deltas_and_assumptions: list[str] = Field(
        min_length=3,
        max_length=5,
        description="3-5 crisp bullets detailing revision deltas and load-bearing assumptions.",
    )
    toughest_questions: list[StakeholderQAItem] = Field(
        min_length=5,
        max_length=5,
        description="Exactly 5 tough stakeholder questions and recommended answers.",
    )


class PursuitAuditVerdict(BaseModel):
    """Top-level structured output contract for the 4-Gate Pursuit Deliverable Auditor."""

    model_config = ConfigDict(extra="forbid")

    verdict: Literal["PASS", "REVISE"] = Field(
        description="PASS only when blocking_fixes is empty; REVISE otherwise."
    )
    bluf_summary: str = Field(description="Single-sentence Bottom Line Up Front verdict summary.")
    blocking_fixes: list[GateFinding] = Field(
        default_factory=list,
        description="All BLOCKING issues across Gates 1-3.",
    )
    optional_polish: list[str] = Field(
        default_factory=list,
        max_length=3,
        description="Up to 3 non-blocking polish recommendations.",
    )
    executive_brief: ExecutiveComprehensionBrief = Field(
        description="Mandatory Gate 4 1-page Executive Comprehension & Q&A Brief."
    )

    @model_validator(mode="after")
    def _enforce_verdict_consistency(self) -> PursuitAuditVerdict:
        if self.blocking_fixes and self.verdict != "REVISE":
            raise ValueError("Verdict MUST be 'REVISE' whenever blocking_fixes is non-empty.")
        if not self.blocking_fixes and self.verdict != "PASS":
            raise ValueError("Verdict MUST be 'PASS' when blocking_fixes is empty.")
        return self


class SelfEvaluationResult(BaseModel):
    """Structured output emitted by the Self-Evaluator inside the bounded LoopAgent."""

    model_config = ConfigDict(extra="forbid")

    passed: bool = Field(
        description="True when the audit report satisfies every rubric, lineage, and safety check."
    )
    verdict_consistent: bool = Field(
        description="True when PASS/REVISE matches the presence or absence of BLOCKING findings."
    )
    all_numbers_traced: bool = Field(
        description="True when every headline number is traced in the Numbers Lineage table."
    )
    qa_brief_complete: bool = Field(
        description="True when Gate 4 contains lineage, 3-5 assumptions, and 5 tailored Q&As."
    )
    audience_safe: bool = Field(
        description="True when no internal rate cards or pricing secrets leak to external audiences."
    )
    refinement_instructions: list[str] = Field(
        default_factory=list,
        description="Targeted corrections for Gate 4 synthesis if passed is False.",
    )


# ---------------------------------------------------------------------------
# Gate 1 — Financial & Rate-Card Reconciliation Tool Schemas
# ---------------------------------------------------------------------------


class WorkstreamFinancialRow(BaseModel):
    """Single workstream row extracted from a deliverable's funding or credit table."""

    model_config = ConfigDict(extra="forbid")

    rank: int = Field(ge=1, description="Display order of the workstream (1 = top row).")
    name: str = Field(min_length=2, description="Workstream title.")
    role: str = Field(default="", description="Consulting role / positioning label.")
    steady_state_arr_usd: float = Field(ge=0, description="Direct steady-state ARR in USD.")
    gsf_pso_usd: float = Field(ge=0, description="Cloud consulting funding (GSF/PSO) in USD.")
    psf_partner_usd: float = Field(ge=0, description="Partner services funding (PSF) in USD.")
    total_cost_usd: float = Field(
        ge=0, description="Reported 'Total cost to migrate/implement' in USD."
    )
    duration_months: int = Field(ge=1, le=120, description="Workstream duration in months.")
    planned_fte_months: float = Field(
        ge=0, description="Total planned consulting FTE-months for rate-card reconciliation."
    )
    rate_tier: RateTier = Field(
        default="blended_50_50_fte",
        description="Synthetic benchmark rate tier applied to this workstream.",
    )
    outcome_text: str = Field(
        default="", description="Deliverable outcome description from the table cell."
    )


class AuthoritativeBaselineRequest(BaseModel):
    """Input schema for `fetch_authoritative_deal_baseline`."""

    model_config = ConfigDict(extra="forbid")

    deal_id: str = Field(
        min_length=3,
        description="Synthetic opportunity identifier (e.g., 'OPP-SYNTH-2026-001').",
    )


class AuthoritativeBaselineResponse(BaseModel):
    """Output schema for `fetch_authoritative_deal_baseline`."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["success"] = "success"
    deal_id: str
    account_name: str
    engagement_title: str
    source_citation: str
    duration_months: int
    total_tcv_usd: float
    steady_state_arr_usd: float
    funding_envelope: dict[str, float]
    workstreams: list[dict[str, Any]]
    rate_card_benchmarks_usd_per_month: dict[str, Any]


class FinancialReconciliationRequest(BaseModel):
    """Input schema for `reconcile_financial_rollups_and_rate_cards`."""

    model_config = ConfigDict(extra="forbid")

    deal_id: str = Field(min_length=3, description="Authoritative synthetic deal identifier.")
    reported_summary_total_cost_usd: float = Field(
        ge=0, description="Headline Total Migration/Implementation Cost from Executive Summary."
    )
    reported_summary_gsf_usd: float = Field(
        ge=0, description="Headline GSF (PSO) total from Executive Summary."
    )
    reported_summary_psf_usd: float = Field(
        ge=0, description="Headline PSF (Partner) total from Executive Summary."
    )
    reported_summary_duration_months: int = Field(
        ge=1, le=120, description="Headline migration duration in months from Executive Summary."
    )
    reported_source_tab: str = Field(
        min_length=2,
        description="Workbook tab cited by the deliverable (verified against authoritative tab).",
    )
    workstreams: list[WorkstreamFinancialRow] = Field(
        min_length=1,
        description="Detailed workstream rows from deep-dive tables and resourcing timelines.",
    )


class FinancialReconciliationReport(BaseModel):
    """Output schema for `reconcile_financial_rollups_and_rate_cards`."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["success"] = "success"
    gate: Literal["Gate 1"] = "Gate 1"
    deal_id: str
    passed: bool
    computed_totals: dict[str, float]
    authoritative_source_citation: str
    findings: list[GateFinding]
    verified_lineage_rows: list[NumbersLineageRow]


# ---------------------------------------------------------------------------
# Gate 2 — Commercial, Posture & Confidentiality Guardrail Schemas
# ---------------------------------------------------------------------------


class CommercialPostureAuditRequest(BaseModel):
    """Input schema for `inspect_commercial_posture_and_leak_guardrails`."""

    model_config = ConfigDict(extra="forbid")

    deliverable_type: DeliverableType = Field(description="Category of consulting deliverable.")
    audience: AudienceType = Field(description="Intended target audience.")
    full_text: str = Field(min_length=10, description="Concatenated slide or document text.")
    table_headers: list[str] = Field(
        default_factory=list,
        description="Exact column headers used in the funding/credit summary table.",
    )
    workstream_names_in_order: list[str] = Field(
        default_factory=list,
        description="Ordered list of workstream names as presented in the deliverable.",
    )
    workstream_arr_map: dict[str, float] = Field(
        default_factory=dict,
        description="Map of workstream name to reported steady-state ARR in USD.",
    )
    is_advisory_control_tower: bool = Field(
        default=True,
        description="True when consulting posture is Control Tower / advisory rather than SI execution.",
    )


class CommercialPostureReport(BaseModel):
    """Output schema for `inspect_commercial_posture_and_leak_guardrails`."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["success"] = "success"
    gate: Literal["Gate 2"] = "Gate 2"
    audience: AudienceType
    deliverable_type: DeliverableType
    passed: bool
    external_leak_detected: bool
    findings: list[GateFinding]


# ---------------------------------------------------------------------------
# Gate 3 — Executive Aesthetic & Visual Density Schemas
# ---------------------------------------------------------------------------


class SlideVisualMetadata(BaseModel):
    """Structured visual/layout inspection payload for a single slide or section."""

    model_config = ConfigDict(extra="forbid")

    slide_number: int = Field(ge=1)
    title: str = Field(min_length=1)
    has_top_left_kicker: bool = Field(default=True)
    has_title_divider_line: bool = Field(default=False)
    uses_rounded_corners: bool = Field(default=False)
    hex_colors_used: list[str] = Field(default_factory=list)
    font_sizes_pt: list[float] = Field(default_factory=list)
    table_cells: list[str] = Field(default_factory=list)
    body_text: str = Field(default="")


class AestheticDensityAuditRequest(BaseModel):
    """Input schema for `audit_executive_aesthetic_and_density`."""

    model_config = ConfigDict(extra="forbid")

    deliverable_type: DeliverableType
    slides: list[SlideVisualMetadata] = Field(
        min_length=1,
        description="Per-slide or per-section visual styling and text density telemetry.",
    )


class AestheticDensityReport(BaseModel):
    """Output schema for `audit_executive_aesthetic_and_density`."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["success"] = "success"
    gate: Literal["Gate 3"] = "Gate 3"
    passed: bool
    slides_audited: int
    blocking_count: int
    polish_count: int
    findings: list[GateFinding]


# ---------------------------------------------------------------------------
# Memory / Precedent Search & HITL Tool Schemas
# ---------------------------------------------------------------------------


class PrecedentSearchRequest(BaseModel):
    """Input schema for `retrieve_historical_audit_precedents`."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=3, description="Natural-language description of the audit topic.")
    gate_filter: GateName | None = Field(default=None, description="Optional gate filter.")
    top_k: int = Field(default=3, ge=1, le=10)


class PrecedentMatch(BaseModel):
    """Single historical audit precedent retrieved from the persistent vector store."""

    model_config = ConfigDict(extra="forbid")

    precedent_id: str
    gate: str
    deliverable_type: str
    pattern_title: str
    summary: str
    resolution: str
    similarity_score: float


class PrecedentSearchResponse(BaseModel):
    """Output schema for `retrieve_historical_audit_precedents`."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["success"] = "success"
    query: str
    backend: str
    matches: list[PrecedentMatch]


class LineageCitationRequest(BaseModel):
    """Input schema for `record_numbers_lineage_citation`."""

    model_config = ConfigDict(extra="forbid")

    metric: str = Field(min_length=2)
    value: str = Field(min_length=1)
    exact_source: str = Field(min_length=3)
    derivation: str = Field(min_length=3)


class HumanWaiverSignoffRequest(BaseModel):
    """Input schema for HITL `request_human_waiver_signoff`."""

    model_config = ConfigDict(extra="forbid")

    deal_id: str = Field(min_length=3)
    finding_id: str = Field(min_length=2)
    waiver_justification: str = Field(min_length=15)
    approver_role: Literal["Pursuit Lead", "Practice Director", "Pricing Committee"]


class DeliverableCertificationRequest(BaseModel):
    """Input schema for HITL `certify_deliverable_for_external_release`."""

    model_config = ConfigDict(extra="forbid")

    deal_id: str = Field(min_length=3)
    deliverable_title: str = Field(min_length=3)
    audience: AudienceType
    verdict: Literal["PASS", "REVISE"]
    blocking_count: int = Field(ge=0)
    total_deal_value_usd: float = Field(ge=0)


class DeliverableCertificationResponse(BaseModel):
    """Output schema for HITL `certify_deliverable_for_external_release`."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["certified", "pending_human_confirmation", "rejected"]
    deal_id: str
    deliverable_title: str
    audience: AudienceType
    human_confirmed: bool
    certification_note: str


def export_tool_json_schemas() -> dict[str, dict[str, Any]]:
    """Return strict JSON Schema definitions for all Pursuit Auditor tool contracts."""
    models: list[type[BaseModel]] = [
        AuthoritativeBaselineRequest,
        AuthoritativeBaselineResponse,
        FinancialReconciliationRequest,
        FinancialReconciliationReport,
        CommercialPostureAuditRequest,
        CommercialPostureReport,
        AestheticDensityAuditRequest,
        AestheticDensityReport,
        PrecedentSearchRequest,
        PrecedentSearchResponse,
        LineageCitationRequest,
        HumanWaiverSignoffRequest,
        DeliverableCertificationRequest,
        DeliverableCertificationResponse,
        PursuitAuditVerdict,
        SelfEvaluationResult,
        ToolErrorResponse,
    ]
    return {model.__name__: model.model_json_schema() for model in models}
