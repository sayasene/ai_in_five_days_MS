"""Multi-agent 4-gate audit workflow factories (Sequential + Parallel + Bounded Loop).

Architecture:
    SequentialAgent("four_gate_audit_pipeline")
    ├── intake_normalizer_agent            [Flash] — Baseline & precedent loader
    ├── ParallelAgent("parallel_gate_auditors")
    │   ├── gate1_math_reconciler_agent    [Pro]   — Rollups, tab parity, rate cards
    │   ├── gate2_commercial_posture_agent [Pro]   — Advisory posture, leak & SOW check
    │   └── gate3_aesthetic_density_agent  [Flash] — Monochrome aesthetic & cell density
    └── LoopAgent("brief_and_self_eval_loop", max_iterations=2)
        ├── gate4_executive_brief_agent    [Pro]   — Verdict + 1-Page Executive Q&A Brief
        ├── self_evaluator_agent           [Pro]   — Structured Pydantic Self-Eval
        └── audit_escalation_checker       [Base]  — Stops loop immediately on pass
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from google.adk.agents import Agent, BaseAgent, LoopAgent, ParallelAgent, SequentialAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.genai import types as genai_types

from pursuit_auditor.config.settings import AuditorSettings, get_settings
from pursuit_auditor.memory.vector_store import build_vertex_ai_search_tool
from pursuit_auditor.observability.logging import get_logger
from pursuit_auditor.prompts import (
    GATE1_MATH_RECONCILER_INSTRUCTION,
    GATE2_COMMERCIAL_POSTURE_INSTRUCTION,
    GATE3_AESTHETIC_DENSITY_INSTRUCTION,
    GATE4_EXECUTIVE_BRIEF_INSTRUCTION,
    INTAKE_NORMALIZER_INSTRUCTION,
    SELF_EVALUATOR_INSTRUCTION,
)
from pursuit_auditor.routing import ModelRouter
from pursuit_auditor.schemas import SelfEvaluationResult
from pursuit_auditor.tools import (
    audit_executive_aesthetic_and_density,
    fetch_authoritative_deal_baseline,
    inspect_commercial_posture_and_leak_guardrails,
    reconcile_financial_rollups_and_rate_cards,
    record_numbers_lineage_citation,
    retrieve_historical_audit_precedents,
)

logger = get_logger(__name__)


class AuditEscalationChecker(BaseAgent):
    """Deterministic `BaseAgent` exiting the bounded `LoopAgent` once self-eval passes."""

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        raw_eval: Any = ctx.session.state.get("self_eval_result")
        passed = False
        if isinstance(raw_eval, dict):
            passed = bool(raw_eval.get("passed", False))
        elif hasattr(raw_eval, "passed"):
            passed = bool(raw_eval.passed)

        logger.info(
            "self_eval_loop_checkpoint",
            agent_name=self.name,
            self_eval_passed=passed,
        )
        if passed:
            yield Event(author=self.name, actions=EventActions(escalate=True))
        else:
            yield Event(author=self.name)


def create_intake_normalizer_agent(
    router: ModelRouter,
    settings: AuditorSettings | None = None,
) -> Agent:
    """Factory for Stage 1 intake & baseline normalizer (Fast tier)."""
    tools: list[Any] = [
        fetch_authoritative_deal_baseline,
        retrieve_historical_audit_precedents,
        PreloadMemoryTool(),
    ]
    enterprise_search = build_vertex_ai_search_tool(settings)
    if enterprise_search is not None:
        tools.append(enterprise_search)

    return Agent(
        name="intake_normalizer_agent",
        model=router.model_for_agent("intake_normalizer_agent"),
        description="Loads authoritative deal baselines and historical audit precedents into session state.",
        instruction=INTAKE_NORMALIZER_INSTRUCTION,
        tools=tools,
        generate_content_config=genai_types.GenerateContentConfig(temperature=0.1),
        output_key="intake_summary",
    )


def create_gate1_math_reconciler_agent(router: ModelRouter) -> Agent:
    """Factory for Gate 1 Math & Rate-Card Reconciliation specialist (Reasoning tier)."""
    return Agent(
        name="gate1_math_reconciler_agent",
        model=router.model_for_agent("gate1_math_reconciler_agent"),
        description="Audits row sums, cross-slide rollups, authoritative tab parity, and FTE rate-card math.",
        instruction=GATE1_MATH_RECONCILER_INSTRUCTION,
        tools=[
            fetch_authoritative_deal_baseline,
            reconcile_financial_rollups_and_rate_cards,
            record_numbers_lineage_citation,
        ],
        generate_content_config=genai_types.GenerateContentConfig(temperature=0.1),
        output_key="gate1_result",
    )


def create_gate2_commercial_posture_agent(router: ModelRouter) -> Agent:
    """Factory for Gate 2 Commercial Posture & Leak Guardrails specialist (Reasoning tier)."""
    return Agent(
        name="gate2_commercial_posture_agent",
        model=router.model_for_agent("gate2_commercial_posture_agent"),
        description="Audits advisory vs. execution posture, external pricing leaks, strategy table conventions, and SOW clauses.",
        instruction=GATE2_COMMERCIAL_POSTURE_INSTRUCTION,
        tools=[
            inspect_commercial_posture_and_leak_guardrails,
            retrieve_historical_audit_precedents,
        ],
        generate_content_config=genai_types.GenerateContentConfig(temperature=0.1),
        output_key="gate2_result",
    )


def create_gate3_aesthetic_density_agent(router: ModelRouter) -> Agent:
    """Factory for Gate 3 Executive Aesthetic & Visual Density specialist (Fast tier)."""
    return Agent(
        name="gate3_aesthetic_density_agent",
        model=router.model_for_agent("gate3_aesthetic_density_agent"),
        description="Audits monochrome palette, 0px geometry, 10-24pt typography, 15-word table cell ceiling, and tone.",
        instruction=GATE3_AESTHETIC_DENSITY_INSTRUCTION,
        tools=[audit_executive_aesthetic_and_density],
        generate_content_config=genai_types.GenerateContentConfig(temperature=0.1),
        output_key="gate3_result",
    )


def create_gate4_executive_brief_agent(router: ModelRouter) -> Agent:
    """Factory for Gate 4 Verdict & 1-Page Executive Comprehension & Q&A Brief synthesizer (Reasoning tier)."""
    return Agent(
        name="gate4_executive_brief_agent",
        model=router.model_for_agent("gate4_executive_brief_agent"),
        description="Synthesizes PASS/REVISE verdict, Blocking Fixes table, and mandatory 1-page Executive Q&A Brief.",
        instruction=GATE4_EXECUTIVE_BRIEF_INSTRUCTION,
        tools=[record_numbers_lineage_citation],
        generate_content_config=genai_types.GenerateContentConfig(temperature=0.2),
        output_key="executive_brief",
    )


def create_self_evaluator_agent(router: ModelRouter) -> Agent:
    """Factory for strict Pydantic `SelfEvaluationResult` judge inside the bounded loop."""
    return Agent(
        name="self_evaluator_agent",
        model=router.model_for_agent("self_evaluator_agent"),
        description="Critically evaluates the Gate 4 audit report for verdict consistency, lineage parity, and audience safety.",
        instruction=SELF_EVALUATOR_INSTRUCTION,
        output_schema=SelfEvaluationResult,
        output_key="self_eval_result",
        generate_content_config=genai_types.GenerateContentConfig(temperature=0.0),
    )


def create_four_gate_audit_pipeline(
    router: ModelRouter | None = None,
    settings: AuditorSettings | None = None,
) -> SequentialAgent:
    """Assemble the full multi-agent 4-gate audit pipeline (Sequential -> Parallel -> Bounded Loop)."""
    cfg = settings or get_settings()
    model_router = router or ModelRouter(cfg)

    parallel_gates = ParallelAgent(
        name="parallel_gate_auditors",
        description="Concurrently executes Gate 1 (Math), Gate 2 (Commercial), and Gate 3 (Aesthetic) specialists.",
        sub_agents=[
            create_gate1_math_reconciler_agent(model_router),
            create_gate2_commercial_posture_agent(model_router),
            create_gate3_aesthetic_density_agent(model_router),
        ],
    )

    bounded_synthesis_loop = LoopAgent(
        name="brief_and_self_eval_loop",
        description="Synthesizes the Gate 4 Executive Brief and verifies quality via bounded self-evaluation (max 2 passes).",
        sub_agents=[
            create_gate4_executive_brief_agent(model_router),
            create_self_evaluator_agent(model_router),
            AuditEscalationChecker(
                name="audit_escalation_checker",
                description="Exits refinement loop immediately once SelfEvaluationResult.passed is True.",
            ),
        ],
        max_iterations=cfg.max_self_eval_iterations,
    )

    return SequentialAgent(
        name="four_gate_audit_pipeline",
        description="End-to-end 4-Gate Pursuit Deliverable Audit pipeline with parallel specialists and bounded self-eval.",
        sub_agents=[
            create_intake_normalizer_agent(model_router, cfg),
            parallel_gates,
            bounded_synthesis_loop,
        ],
    )
