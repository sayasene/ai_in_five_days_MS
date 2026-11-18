"""Root ADK `App` and multi-agent Coordinator for the Pursuit Deliverable Auditor.

Wires together:
- Multi-agent orchestration (`pursuit_audit_coordinator` -> `four_gate_audit_pipeline`)
- Context compaction (`EventsCompactionConfig`, `ContextCacheConfig`, `ContextFilterPlugin`)
- Guardrails & Policy Plugins (`GlobalInstructionPlugin`, `ReadOnlyEnforcementPlugin`,
  `ModelArmorPolicyPlugin`, `DynamicModelRoutingPlugin`, `IntentOutcomeAuditPlugin`,
  `SelfEvaluationGuardrailPlugin`, `ReflectAndRetryToolPlugin`, `AsyncMemoryPersistencePlugin`)
- Resumable Human-in-the-Loop confirmations (`ResumabilityConfig(is_resumable=True)`)
- Persistent session state (`DatabaseSessionService` / `VertexAiSessionService`)
- Secure runtime secret hydration (`Google Cloud Secret Manager`)
"""

from __future__ import annotations

import os

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.apps.app import ResumabilityConfig
from google.adk.plugins.context_filter_plugin import ContextFilterPlugin
from google.adk.plugins.global_instruction_plugin import GlobalInstructionPlugin
from google.adk.plugins.reflect_retry_tool_plugin import ReflectAndRetryToolPlugin
from google.adk.runners import Runner
from google.genai import types as genai_types

from pursuit_auditor.config.secrets import get_secret_provider
from pursuit_auditor.config.settings import AuditorSettings, get_settings
from pursuit_auditor.memory import (
    build_context_cache_config,
    build_events_compaction_config,
    build_memory_service,
    build_session_service,
)
from pursuit_auditor.observability.logging import configure_structured_logging, get_logger
from pursuit_auditor.observability.tracing import configure_tracing
from pursuit_auditor.plugins import (
    AsyncMemoryPersistencePlugin,
    IntentOutcomeAuditPlugin,
    ModelArmorPolicyPlugin,
    ReadOnlyEnforcementPlugin,
    SelfEvaluationGuardrailPlugin,
)
from pursuit_auditor.prompts import COORDINATOR_INSTRUCTION, GLOBAL_AUDITOR_CONSTITUTION
from pursuit_auditor.routing import DynamicModelRoutingPlugin, ModelRouter
from pursuit_auditor.sub_agents import create_four_gate_audit_pipeline
from pursuit_auditor.tools import (
    certify_deliverable_tool,
    fetch_authoritative_deal_baseline,
    request_waiver_tool,
    retrieve_historical_audit_precedents,
)

logger = get_logger(__name__)


def _bootstrap_runtime(settings: AuditorSettings) -> None:
    """Initialize structured logging, OpenTelemetry tracing, and Secret Manager key hydration."""
    configure_structured_logging(level=settings.log_level)
    configure_tracing(
        service_name=settings.otel_service_name,
        exporter_mode=settings.otel_exporter_mode,
    )
    if (
        settings.enable_secret_manager
        and not settings.google_genai_use_vertexai
        and not os.getenv("GOOGLE_API_KEY")
    ):
        provider = get_secret_provider(
            project_id=settings.google_cloud_project,
            use_secret_manager=True,
        )
        api_key = provider.get_secret(
            secret_id=settings.gemini_api_key_secret_name,
            env_fallback="GOOGLE_API_KEY",
            required=False,
        )
        if api_key:
            os.environ["GOOGLE_API_KEY"] = api_key


def build_root_agent(settings: AuditorSettings | None = None) -> Agent:
    """Create a fresh Coordinator `root_agent` with the 4-gate pipeline and HITL tools."""
    cfg = settings or get_settings()
    router = ModelRouter(cfg)
    pipeline = create_four_gate_audit_pipeline(router=router, settings=cfg)

    return Agent(
        name="pursuit_audit_coordinator",
        model=router.model_for_agent("pursuit_audit_coordinator"),
        description=(
            "Root coordinator for the read-only Pursuit Deliverable Auditor. Delegates full "
            "4-gate deliverable audits to the multi-agent pipeline and governs HITL sign-offs."
        ),
        instruction=COORDINATOR_INSTRUCTION,
        sub_agents=[pipeline],
        tools=[
            fetch_authoritative_deal_baseline,
            retrieve_historical_audit_precedents,
            request_waiver_tool,
            certify_deliverable_tool,
        ],
        generate_content_config=genai_types.GenerateContentConfig(temperature=0.1),
    )


def build_app(settings: AuditorSettings | None = None) -> App:
    """Build the full ADK `App` with compaction, caching, resumable HITL, and policy plugins."""
    cfg = settings or get_settings()
    _bootstrap_runtime(cfg)
    router = ModelRouter(cfg)
    coordinator = build_root_agent(cfg)

    plugins = [
        GlobalInstructionPlugin(global_instruction=GLOBAL_AUDITOR_CONSTITUTION),
        ContextFilterPlugin(num_invocations_to_keep=cfg.context_filter_keep_invocations),
        ReadOnlyEnforcementPlugin(),
        ModelArmorPolicyPlugin(settings=cfg),
        DynamicModelRoutingPlugin(router=router),
        IntentOutcomeAuditPlugin(),
        SelfEvaluationGuardrailPlugin(),
        ReflectAndRetryToolPlugin(max_retries=2, throw_exception_if_retry_exceeded=False),
        AsyncMemoryPersistencePlugin(),
    ]

    adk_app = App(
        name=cfg.app_name,
        root_agent=coordinator,
        plugins=plugins,
        events_compaction_config=build_events_compaction_config(cfg),
        context_cache_config=build_context_cache_config(cfg),
        resumability_config=ResumabilityConfig(is_resumable=True),
    )
    logger.info(
        "pursuit_auditor_app_initialized",
        app_name=cfg.app_name,
        fast_model=cfg.pursuit_auditor_fast_model,
        reasoning_model=cfg.pursuit_auditor_reasoning_model,
        plugin_count=len(plugins),
    )
    return adk_app


def create_runner(settings: AuditorSettings | None = None) -> Runner:
    """Create an ADK `Runner` backed by persistent sessions and async memory consolidation."""
    cfg = settings or get_settings()
    adk_app = build_app(cfg)
    session_service = build_session_service(cfg)
    memory_service = build_memory_service(cfg)
    return Runner(
        app=adk_app,
        session_service=session_service,
        memory_service=memory_service,
        auto_create_session=True,
    )


# Standard ADK discovery exports (`adk web`, `adk run`, `agents-cli`)
app = build_app()
root_agent = app.root_agent
