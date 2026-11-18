"""Security, read-only, Model Armor, self-eval, and async memory ADK plugins.

Registered at the `App(plugins=[...])` boundary so every agent, model call, and
tool call inherits deterministic policy enforcement before execution.
"""

from __future__ import annotations

import importlib
import re
from typing import Any, Protocol

from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.invocation_context import InvocationContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools import BaseTool, ToolContext
from google.genai import types as genai_types

from pursuit_auditor.config.settings import AuditorSettings, get_settings
from pursuit_auditor.memory.async_memory import (
    AsyncMemoryConsolidator,
    MemoryConsolidationJob,
    get_memory_consolidator,
)
from pursuit_auditor.observability.logging import get_logger
from pursuit_auditor.observability.pii_redactor import get_pii_redactor
from pursuit_auditor.observability.tracing import traced_span
from pursuit_auditor.schemas import ToolErrorResponse

logger = get_logger(__name__)

# Mutating prefixes or SQL DML operations strictly forbidden by the Read-Only Contract
_MUTATING_TOOL_PREFIXES: tuple[str, ...] = (
    "mutate_",
    "delete_",
    "drop_",
    "write_",
    "overwrite_",
    "update_",
    "edit_",
    "patch_",
    "truncate_",
)

_SQL_DML_PATTERN = re.compile(
    r"\b(?:DROP\s+TABLE|DELETE\s+FROM|TRUNCATE\s+TABLE|UPDATE\s+\w+\s+SET|INSERT\s+INTO)\b",
    re.IGNORECASE,
)

_PROMPT_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "instruction_override",
        re.compile(
            r"(?:ignore|disregard|forget)\s+(?:all\s+)?(?:previous|prior|above|system)\s+instructions",
            re.IGNORECASE,
        ),
    ),
    (
        "system_prompt_exfiltration",
        re.compile(
            r"(?:reveal|print|dump|output)\s+(?:your\s+)?(?:system\s+prompt|hidden\s+instructions|api\s+key|secret)",
            re.IGNORECASE,
        ),
    ),
    (
        "verdict_tampering",
        re.compile(
            r"(?:always\s+return|force\s+the\s+verdict\s+to)\s+PASS\s+(?:even\s+if|regardless)",
            re.IGNORECASE,
        ),
    ),
)

_EXTERNAL_RATE_LEAK_RE = re.compile(
    r"\$\s*\d{2,3}\s*[kK]\s*/\s*(?:mo|month)|internal\s+rate\s+card|gsf[- ]to[- ]psf\s+margin",
    re.IGNORECASE,
)


class ModelArmorBackend(Protocol):
    """Protocol abstracting Google Cloud Model Armor prompt/response screening."""

    def screen_prompt(self, text: str) -> tuple[bool, str]:
        """Return `(allowed, reason)` after screening user/model input."""
        ...

    def screen_response(self, text: str) -> tuple[bool, str]:
        """Return `(allowed, reason)` after screening model output."""
        ...


class GoogleCloudModelArmorBackend:
    """Production Google Cloud Model Armor (`google-cloud-modelarmor`) client wrapper."""

    def __init__(self, project_id: str, location: str, template_id: str) -> None:
        self._template_name = (
            f"projects/{project_id}/locations/{location}/templates/{template_id}"
        )
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            modelarmor_v1 = importlib.import_module("google.cloud.modelarmor_v1")
            self._client = modelarmor_v1.ModelArmorClient()
        return self._client

    def screen_prompt(self, text: str) -> tuple[bool, str]:
        modelarmor_v1 = importlib.import_module("google.cloud.modelarmor_v1")
        client = self._get_client()
        req = modelarmor_v1.SanitizeUserPromptRequest(
            name=self._template_name,
            user_prompt_data=modelarmor_v1.DataItem(text=text),
        )
        resp = client.sanitize_user_prompt(request=req)
        match_state = str(resp.sanitization_result.filter_match_state)
        if "MATCH_FOUND" in match_state:
            return False, f"Google Cloud Model Armor blocked prompt ({match_state})."
        return True, "ok"

    def screen_response(self, text: str) -> tuple[bool, str]:
        modelarmor_v1 = importlib.import_module("google.cloud.modelarmor_v1")
        client = self._get_client()
        req = modelarmor_v1.SanitizeModelResponseRequest(
            name=self._template_name,
            model_response_data=modelarmor_v1.DataItem(text=text),
        )
        resp = client.sanitize_model_response(request=req)
        match_state = str(resp.sanitization_result.filter_match_state)
        if "MATCH_FOUND" in match_state:
            return False, f"Google Cloud Model Armor blocked response ({match_state})."
        return True, "ok"


class ReadOnlyEnforcementPlugin(BasePlugin):
    """Hard-blocks any mutating or destructive tool invocation across all agents."""

    def __init__(self) -> None:
        super().__init__(name="read_only_enforcement_plugin")

    async def before_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
    ) -> dict[str, Any] | None:
        """Intercept and block forbidden write/delete tool calls or SQL DML arguments."""
        tool_name = getattr(tool, "name", str(tool)).lower()
        serialized_args = str(tool_args)

        violation_reason: str | None = None
        if any(tool_name.startswith(prefix) for prefix in _MUTATING_TOOL_PREFIXES):
            violation_reason = (
                f"Tool '{tool_name}' violates the Strict Read-Only Auditor Contract."
            )
        elif _SQL_DML_PATTERN.search(serialized_args):
            violation_reason = (
                "Destructive or mutating SQL statement detected inside tool arguments."
            )

        if violation_reason:
            logger.warning(
                "read_only_guardrail_blocked_tool",
                tool_name=tool_name,
                reason=violation_reason,
            )
            return ToolErrorResponse(
                error_code="READ_ONLY_POLICY_VIOLATION",
                recoverable=False,
                message=violation_reason,
                recovery_instructions=[
                    "The Pursuit Deliverable Auditor is strictly read-only.",
                    "Do NOT attempt to edit slides, docs, databases, or CRM records.",
                    "Instead, document the exact fix instruction in the Blocking Fixes table.",
                ],
            ).model_dump()
        return None


class ModelArmorPolicyPlugin(BasePlugin):
    """Guards prompts and responses using Google Cloud Model Armor + deterministic rules.

    Protects against prompt injection, verdict tampering, credential exfiltration,
    and external pricing/rate-card leakage for customer/partner audiences.
    """

    def __init__(
        self,
        *,
        settings: AuditorSettings | None = None,
        backend: ModelArmorBackend | None = None,
    ) -> None:
        super().__init__(name="model_armor_policy_plugin")
        self._settings = settings or get_settings()
        self._backend = backend

    def _get_backend(self) -> ModelArmorBackend | None:
        if self._backend is not None:
            return self._backend
        if (
            self._settings.enable_model_armor
            and self._settings.google_cloud_project
            and self._settings.model_armor_template_id
        ):
            self._backend = GoogleCloudModelArmorBackend(
                project_id=self._settings.google_cloud_project,
                location=self._settings.google_cloud_location,
                template_id=self._settings.model_armor_template_id,
            )
            return self._backend
        return None

    async def before_model_callback(
        self,
        *,
        callback_context: CallbackContext,
        llm_request: LlmRequest,
    ) -> LlmResponse | None:
        """Screen incoming prompts for injection/tampering before calling the LLM."""
        user_chunks: list[str] = []
        for content in getattr(llm_request, "contents", None) or []:
            if getattr(content, "role", None) == "user":
                for part in getattr(content, "parts", None) or []:
                    if getattr(part, "text", None):
                        user_chunks.append(part.text)
        prompt_text = "\n".join(user_chunks)
        if not prompt_text:
            return None

        with traced_span("audit.guardrail.screen_prompt"):
            # 1. Google Cloud Model Armor screening when configured
            backend = self._get_backend()
            if backend is not None:
                try:
                    allowed, reason = backend.screen_prompt(prompt_text)
                    if not allowed:
                        logger.warning("model_armor_prompt_blocked", reason=reason)
                        return LlmResponse(
                            content=genai_types.Content(
                                role="model",
                                parts=[
                                    genai_types.Part.from_text(
                                        text=f"[POLICY GUARDRAIL BLOCKED] {reason}"
                                    )
                                ],
                            )
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "model_armor_backend_fallback",
                        error_type=type(exc).__name__,
                    )

            # 2. Deterministic local prompt-injection & verdict-tampering rules
            for rule_name, pattern in _PROMPT_INJECTION_PATTERNS:
                if pattern.search(prompt_text):
                    logger.warning("local_policy_prompt_blocked", rule=rule_name)
                    return LlmResponse(
                        content=genai_types.Content(
                            role="model",
                            parts=[
                                genai_types.Part.from_text(
                                    text=(
                                        "[POLICY GUARDRAIL BLOCKED] Request triggered "
                                        f"security guardrail '{rule_name}'. The Pursuit Auditor "
                                        "cannot override system policies or force a PASS verdict."
                                    )
                                )
                            ],
                        )
                    )
        return None

    async def after_model_callback(
        self,
        *,
        callback_context: CallbackContext,
        llm_response: LlmResponse,
    ) -> LlmResponse | None:
        """Scrub residual PII and block accidental rate-card leaks on external audiences."""
        content = getattr(llm_response, "content", None)
        parts = getattr(content, "parts", None) if content else None
        if not parts:
            return None

        redactor = get_pii_redactor()
        state: dict[str, Any] = getattr(callback_context, "state", {})
        audience = str(state.get("target_audience", "internal_leadership"))
        modified = False
        new_parts: list[genai_types.Part] = []

        for part in parts:
            text = getattr(part, "text", None)
            if not text:
                new_parts.append(part)
                continue

            scrub = redactor.redact_text(text)
            clean_text = scrub.redacted_text
            if scrub.redaction_count > 0:
                modified = True
                logger.warning(
                    "outbound_response_pii_redacted",
                    categories=scrub.detected_categories,
                    count=scrub.redaction_count,
                )

            if audience in ("customer", "partner") and _EXTERNAL_RATE_LEAK_RE.search(clean_text):
                modified = True
                clean_text = _EXTERNAL_RATE_LEAK_RE.sub(
                    "[REDACTED:INTERNAL_COMMERCIAL_RATE]", clean_text
                )
                logger.warning(
                    "outbound_external_rate_leak_scrubbed",
                    audience=audience,
                )

            new_parts.append(genai_types.Part.from_text(text=clean_text))

        if modified:
            return LlmResponse(
                content=genai_types.Content(role="model", parts=new_parts)
            )
        return None


class SelfEvaluationGuardrailPlugin(BasePlugin):
    """Enforces verdict consistency after Gate 4 synthesis (prevents false PASS verdicts)."""

    def __init__(self) -> None:
        super().__init__(name="self_evaluation_guardrail_plugin")

    async def after_agent_callback(
        self,
        *,
        agent: BaseAgent,
        callback_context: CallbackContext,
    ) -> genai_types.Content | None:
        """Verify verdict alignment whenever Gate 4 completes."""
        if agent.name != "gate4_executive_brief_agent":
            return None

        state = callback_context.state
        total_blocking = (
            int(state.get("gate1_blocking_count", 0))
            + int(state.get("gate2_blocking_count", 0))
            + int(state.get("gate3_blocking_count", 0))
        )
        brief_text = str(state.get("executive_brief", ""))
        if total_blocking > 0 and re.search(r"\bVerdict\s*:?\s*\*{0,2}\s*PASS\b", brief_text):
            logger.warning(
                "self_eval_guardrail_corrected_false_pass",
                total_blocking=total_blocking,
            )
            corrected = (
                f"**Verdict: REVISE ({total_blocking} BLOCKING issue(s) detected across Gates 1–3)**\n\n"
                "> Guardrail Enforcement Notice: Automated self-evaluation overrode a premature "
                "`PASS` label because one or more `BLOCKING` findings remain open.\n\n"
                + brief_text
            )
            state["executive_brief"] = corrected
            return genai_types.Content(
                role="model", parts=[genai_types.Part.from_text(text=corrected)]
            )
        return None


class AsyncMemoryPersistencePlugin(BasePlugin):
    """Queues non-blocking background memory consolidation after every agent invocation."""

    def __init__(self, consolidator: AsyncMemoryConsolidator | None = None) -> None:
        super().__init__(name="async_memory_persistence_plugin")
        self._consolidator = consolidator

    async def after_run_callback(self, *, invocation_context: InvocationContext) -> None:
        """Schedule background memory consolidation without blocking the response."""
        session = invocation_context.session
        state = session.state or {}
        brief = str(state.get("executive_brief", "")).strip()
        if not brief:
            return

        consolidator = self._consolidator or get_memory_consolidator()
        job = MemoryConsolidationJob(
            session_id=session.id,
            deal_id=str(state.get("active_deal_id", "UNSPECIFIED")),
            deliverable_type=str(state.get("deliverable_type", "services_strategy_deck")),
            summary_text=brief[:1200],
            gate="Gate 4",
            session_obj=session,
        )
        consolidator.enqueue(job)
