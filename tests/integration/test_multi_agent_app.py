"""Integration tests verifying the ADK App, multi-agent graph, escalation loop, and FastAPI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from google.adk.agents import LoopAgent, ParallelAgent, SequentialAgent

from pursuit_auditor.agent import build_app
from pursuit_auditor.config.settings import AuditorSettings
from pursuit_auditor.fast_api_app import healthz
from pursuit_auditor.sub_agents import AuditEscalationChecker


@dataclass
class _StubSession:
    state: dict[str, Any]


@dataclass
class _StubInvocationContext:
    session: _StubSession


def test_adk_app_wires_multi_agent_graph_compaction_cache_hitl_and_plugins() -> None:
    cfg = AuditorSettings(session_backend="in_memory", otel_exporter_mode="memory")
    adk_app = build_app(cfg)

    assert adk_app.name == "pursuit_auditor"
    assert adk_app.events_compaction_config is not None
    assert adk_app.context_cache_config is not None
    assert adk_app.resumability_config is not None
    assert adk_app.resumability_config.is_resumable is True

    plugin_names = {plugin.name for plugin in adk_app.plugins}
    expected_plugins = {
        "global_instruction",
        "context_filter_plugin",
        "read_only_enforcement_plugin",
        "model_armor_policy_plugin",
        "dynamic_model_routing_plugin",
        "intent_outcome_audit_plugin",
        "self_evaluation_guardrail_plugin",
        "reflect_retry_tool_plugin",
        "async_memory_persistence_plugin",
    }
    assert expected_plugins.issubset(plugin_names)

    # Verify multi-agent hierarchy: Coordinator -> Sequential -> [Intake, Parallel(G1,G2,G3), Loop(G4,SE,Esc)]
    coordinator = adk_app.root_agent
    assert coordinator is not None
    assert coordinator.name == "pursuit_audit_coordinator"
    assert len(coordinator.sub_agents) == 1

    pipeline = coordinator.sub_agents[0]
    assert isinstance(pipeline, SequentialAgent)
    assert pipeline.name == "four_gate_audit_pipeline"
    assert len(pipeline.sub_agents) == 3

    intake, parallel_gates, synthesis_loop = pipeline.sub_agents
    assert intake.name == "intake_normalizer_agent"
    assert isinstance(parallel_gates, ParallelAgent)
    assert [a.name for a in parallel_gates.sub_agents] == [
        "gate1_math_reconciler_agent",
        "gate2_commercial_posture_agent",
        "gate3_aesthetic_density_agent",
    ]
    assert isinstance(synthesis_loop, LoopAgent)
    assert synthesis_loop.max_iterations == 2
    assert [a.name for a in synthesis_loop.sub_agents] == [
        "gate4_executive_brief_agent",
        "self_evaluator_agent",
        "audit_escalation_checker",
    ]


@pytest.mark.asyncio
async def test_audit_escalation_checker_exits_loop_only_when_self_eval_passes() -> None:
    checker = AuditEscalationChecker(name="audit_escalation_checker")

    ctx_fail = _StubInvocationContext(session=_StubSession(state={"self_eval_result": {"passed": False}}))
    events_fail = [event async for event in checker._run_async_impl(ctx_fail)]  # type: ignore[arg-type]
    assert len(events_fail) == 1
    assert not events_fail[0].actions.escalate

    ctx_pass = _StubInvocationContext(session=_StubSession(state={"self_eval_result": {"passed": True}}))
    events_pass = [event async for event in checker._run_async_impl(ctx_pass)]  # type: ignore[arg-type]
    assert len(events_pass) == 1
    assert events_pass[0].actions.escalate is True


@pytest.mark.asyncio
async def test_fastapi_healthz_probe_returns_ok() -> None:
    payload = await healthz()
    assert payload["status"] == "ok"
    assert payload["service"] == "pursuit-auditor-agent"
