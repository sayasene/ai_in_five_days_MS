"""Strategic Model Routing between Fast (Flash) and Deep Reasoning (Pro) tiers.

Routes deterministic/low-latency subtasks (intake normalization, Gate 3 visual
density checks, event summarization) to `gemini-2.5-flash` and routes high-stakes
financial reconciliation, commercial/SOW posture auditing, executive brief
synthesis, and self-evaluation to `gemini-2.5-pro`. Also supports dynamic
request-level complexity escalation inside `before_model_callback`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin

from pursuit_auditor.config.settings import AuditorSettings, get_settings
from pursuit_auditor.observability.logging import get_logger
from pursuit_auditor.observability.tracing import traced_span

logger = get_logger(__name__)


class ModelTier(StrEnum):
    """Supported model capability/cost tiers."""

    FAST = "fast"
    REASONING = "reasoning"


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    """Explainable routing choice emitted by `ModelRouter`."""

    tier: ModelTier
    model_name: str
    reason: str
    escalated_dynamically: bool = False


# Static agent-to-tier mapping reflecting task complexity & latency budgets
_AGENT_DEFAULT_TIER: dict[str, ModelTier] = {
    "pursuit_audit_coordinator": ModelTier.FAST,
    "intake_normalizer_agent": ModelTier.FAST,
    "gate3_aesthetic_density_agent": ModelTier.FAST,
    "gate1_math_reconciler_agent": ModelTier.REASONING,
    "gate2_commercial_posture_agent": ModelTier.REASONING,
    "gate4_executive_brief_agent": ModelTier.REASONING,
    "self_evaluator_agent": ModelTier.REASONING,
}

_COMPLEXITY_ESCALATION_PATTERN = re.compile(
    r"\b(?:sow|statement\s+of\s+work|rate\s+card|reconcile|tcv|arr|gsf|psf|waiver|indemnif|liability|pricing\s+committee)\b",
    re.IGNORECASE,
)


class ModelRouter:
    """Selects the optimal Gemini model tier for a given agent and request context."""

    def __init__(self, settings: AuditorSettings | None = None) -> None:
        self._settings = settings or get_settings()

    @property
    def fast_model(self) -> str:
        """Low-latency model for parsing, visual checks, and event summarization."""
        return self._settings.pursuit_auditor_fast_model

    @property
    def reasoning_model(self) -> str:
        """Deep-reasoning model for math reconciliation, legal/commercial audit, and Q&A."""
        return self._settings.pursuit_auditor_reasoning_model

    def model_for_agent(self, agent_name: str) -> str:
        """Return the baseline model configured for a specialist agent."""
        tier = _AGENT_DEFAULT_TIER.get(agent_name, ModelTier.FAST)
        return self.reasoning_model if tier == ModelTier.REASONING else self.fast_model

    def route(
        self,
        *,
        agent_name: str,
        prompt_text: str = "",
        audience: str = "internal_leadership",
        token_estimate: int = 0,
    ) -> RoutingDecision:
        """Resolve the runtime model tier with dynamic complexity escalation."""
        base_tier = _AGENT_DEFAULT_TIER.get(agent_name, ModelTier.FAST)

        if base_tier == ModelTier.REASONING:
            return RoutingDecision(
                tier=ModelTier.REASONING,
                model_name=self.reasoning_model,
                reason=f"Agent '{agent_name}' requires deep reasoning tier by policy.",
            )

        # Dynamically escalate fast-tier coordinator/intake calls when high-stakes
        # commercial, SOW, or external-audience risk signals appear.
        if audience in ("customer", "partner") and agent_name == "pursuit_audit_coordinator":
            return RoutingDecision(
                tier=ModelTier.REASONING,
                model_name=self.reasoning_model,
                reason=f"Escalated to reasoning tier due to external audience '{audience}'.",
                escalated_dynamically=True,
            )

        if (
            agent_name == "pursuit_audit_coordinator"
            and (token_estimate >= 2500 or _COMPLEXITY_ESCALATION_PATTERN.search(prompt_text))
        ):
            return RoutingDecision(
                tier=ModelTier.REASONING,
                model_name=self.reasoning_model,
                reason="Escalated to reasoning tier due to commercial/financial/SOW complexity markers.",
                escalated_dynamically=True,
            )

        return RoutingDecision(
            tier=ModelTier.FAST,
            model_name=self.fast_model,
            reason=f"Agent '{agent_name}' uses fast tier for low-latency structured execution.",
        )


def _extract_prompt_preview(llm_request: LlmRequest) -> str:
    """Concatenate text parts from an ADK `LlmRequest` for complexity inspection."""
    chunks: list[str] = []
    for content in getattr(llm_request, "contents", None) or []:
        for part in getattr(content, "parts", None) or []:
            text = getattr(part, "text", None)
            if text:
                chunks.append(text)
    return "\n".join(chunks)


class DynamicModelRoutingPlugin(BasePlugin):
    """ADK App plugin that applies strategic Flash/Pro model routing before every LLM call."""

    def __init__(self, router: ModelRouter | None = None) -> None:
        super().__init__(name="dynamic_model_routing_plugin")
        self._router = router or ModelRouter()

    async def before_model_callback(
        self,
        *,
        callback_context: CallbackContext,
        llm_request: LlmRequest,
    ) -> LlmResponse | None:
        """Select optimal model tier, update `llm_request.model`, and record telemetry."""
        agent_name = getattr(callback_context, "agent_name", "unknown_agent")
        state: dict[str, Any] = getattr(callback_context, "state", {})
        audience = str(state.get("target_audience", "internal_leadership"))
        prompt_text = _extract_prompt_preview(llm_request)
        token_estimate = max(1, len(prompt_text) // 4)

        decision = self._router.route(
            agent_name=agent_name,
            prompt_text=prompt_text,
            audience=audience,
            token_estimate=token_estimate,
        )

        with traced_span(
            "audit.routing.select_model",
            attributes={
                "agent.name": agent_name,
                "routing.model_tier": decision.tier.value,
                "routing.model_name": decision.model_name,
                "routing.escalated": decision.escalated_dynamically,
            },
        ):
            llm_request.model = decision.model_name
            state["last_routed_model"] = decision.model_name
            state["last_routing_tier"] = decision.tier.value
            logger.info(
                "strategic_model_routed",
                agent_name=agent_name,
                model_tier=decision.tier.value,
                model_name=decision.model_name,
                escalated_dynamically=decision.escalated_dynamically,
                reason=decision.reason,
            )
        return None
