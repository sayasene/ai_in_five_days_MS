"""Intent vs. Outcome structured audit plugin + OpenTelemetry span instrumentation.

For every agent turn and tool invocation, explicitly captures:
1. `INTENT` BEFORE execution: intended tool/agent action, correlation_id,
   PII-redacted input arguments, and expected read-only contract.
2. `OUTCOME` AFTER execution: actual status (`success` / `error` / `blocked`),
   elapsed latency in milliseconds, PII-redacted outcome summary, and whether
   actual outcome matched intended contract.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools import BaseTool, ToolContext
from google.genai import types as genai_types
from opentelemetry.trace import Span, Status, StatusCode

from pursuit_auditor.observability.logging import get_logger
from pursuit_auditor.observability.pii_redactor import get_pii_redactor
from pursuit_auditor.observability.tracing import get_tracer
from pursuit_auditor.schemas import ToolErrorResponse

logger = get_logger(__name__)


@dataclass(slots=True)
class _ActiveExecutionRecord:
    correlation_id: str
    started_monotonic: float
    intended_action: str
    span: Span | None = None


class IntentOutcomeAuditPlugin(BasePlugin):
    """Captures paired INTENT vs. OUTCOME JSON logs and linked OTel spans."""

    def __init__(self) -> None:
        super().__init__(name="intent_outcome_audit_plugin")
        self._in_flight_tools: dict[str, _ActiveExecutionRecord] = {}
        self._in_flight_agents: dict[str, _ActiveExecutionRecord] = {}
        self.recorded_events: list[dict[str, Any]] = []

    def _record(self, payload: dict[str, Any]) -> None:
        self.recorded_events.append(payload)

    async def before_agent_callback(
        self,
        *,
        agent: BaseAgent,
        callback_context: CallbackContext,
    ) -> genai_types.Content | None:
        """Record agent execution INTENT before running a sub-agent."""
        correlation_id = uuid.uuid4().hex[:12]
        invocation_id = getattr(callback_context, "invocation_id", "local")
        key = f"{invocation_id}:{agent.name}"
        tracer = get_tracer("pursuit_auditor.agents")
        span = tracer.start_span(
            f"audit.agent.{agent.name}",
            attributes={
                "agent.name": agent.name,
                "audit.correlation_id": correlation_id,
                "audit.phase": "INTENT",
            },
        )
        self._in_flight_agents[key] = _ActiveExecutionRecord(
            correlation_id=correlation_id,
            started_monotonic=time.perf_counter(),
            intended_action=f"execute_agent:{agent.name}",
            span=span,
        )
        event_payload = {
            "audit_phase": "INTENT",
            "entity_type": "agent",
            "agent_name": agent.name,
            "correlation_id": correlation_id,
            "intended_action": f"execute_agent:{agent.name}",
            "expected_contract": "read_only_audit_step",
        }
        self._record(event_payload)
        logger.info("agent_intent_captured", **event_payload)
        return None

    async def after_agent_callback(
        self,
        *,
        agent: BaseAgent,
        callback_context: CallbackContext,
    ) -> genai_types.Content | None:
        """Record agent execution OUTCOME paired to its prior INTENT correlation_id."""
        invocation_id = getattr(callback_context, "invocation_id", "local")
        key = f"{invocation_id}:{agent.name}"
        record = self._in_flight_agents.pop(key, None)
        correlation_id = record.correlation_id if record else uuid.uuid4().hex[:12]
        latency_ms = (
            round((time.perf_counter() - record.started_monotonic) * 1000.0, 2)
            if record
            else 0.0
        )
        if record and record.span is not None:
            record.span.set_attribute("audit.phase", "OUTCOME")
            record.span.set_attribute("audit.latency_ms", latency_ms)
            record.span.end()

        event_payload = {
            "audit_phase": "OUTCOME",
            "entity_type": "agent",
            "agent_name": agent.name,
            "correlation_id": correlation_id,
            "actual_status": "completed",
            "latency_ms": latency_ms,
            "outcome_matches_intent": True,
        }
        self._record(event_payload)
        logger.info("agent_outcome_captured", **event_payload)
        return None

    async def before_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
    ) -> dict[str, Any] | None:
        """Record tool INTENT with PII-redacted arguments prior to execution."""
        tool_name = getattr(tool, "name", str(tool))
        correlation_id = uuid.uuid4().hex[:12]
        invocation_id = getattr(tool_context, "invocation_id", "local")
        key = f"{invocation_id}:{tool_name}"

        redactor = get_pii_redactor()
        sanitized_args = redactor.redact_payload(tool_args)

        tracer = get_tracer("pursuit_auditor.tools")
        span = tracer.start_span(
            f"audit.tool_call.{tool_name}",
            attributes={
                "tool.name": tool_name,
                "audit.correlation_id": correlation_id,
                "audit.phase": "INTENT",
            },
        )
        self._in_flight_tools[key] = _ActiveExecutionRecord(
            correlation_id=correlation_id,
            started_monotonic=time.perf_counter(),
            intended_action=f"invoke_tool:{tool_name}",
            span=span,
        )

        event_payload = {
            "audit_phase": "INTENT",
            "entity_type": "tool",
            "tool_name": tool_name,
            "correlation_id": correlation_id,
            "intended_action": f"invoke_tool:{tool_name}",
            "sanitized_args": sanitized_args,
            "expected_contract": "schema_validated_read_only_result",
        }
        self._record(event_payload)
        logger.info("tool_intent_captured", **event_payload)
        return None

    async def after_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
        result: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Record paired tool OUTCOME with status, latency, and drift detection."""
        tool_name = getattr(tool, "name", str(tool))
        invocation_id = getattr(tool_context, "invocation_id", "local")
        key = f"{invocation_id}:{tool_name}"
        record = self._in_flight_tools.pop(key, None)
        correlation_id = record.correlation_id if record else uuid.uuid4().hex[:12]
        latency_ms = (
            round((time.perf_counter() - record.started_monotonic) * 1000.0, 2)
            if record
            else 0.0
        )

        redactor = get_pii_redactor()
        sanitized_result = redactor.redact_payload(result)
        actual_status = (
            str(sanitized_result.get("status", "success"))
            if isinstance(sanitized_result, dict)
            else "success"
        )
        outcome_matches_intent = actual_status not in ("error", "rejected")

        if record and record.span is not None:
            record.span.set_attribute("audit.phase", "OUTCOME")
            record.span.set_attribute("audit.actual_status", actual_status)
            record.span.set_attribute("audit.latency_ms", latency_ms)
            record.span.set_attribute("audit.outcome_matches_intent", outcome_matches_intent)
            record.span.end()

        event_payload = {
            "audit_phase": "OUTCOME",
            "entity_type": "tool",
            "tool_name": tool_name,
            "correlation_id": correlation_id,
            "actual_status": actual_status,
            "latency_ms": latency_ms,
            "outcome_matches_intent": outcome_matches_intent,
            "result_keys": sorted(sanitized_result.keys())
            if isinstance(sanitized_result, dict)
            else [],
        }
        self._record(event_payload)
        logger.info("tool_outcome_captured", **event_payload)
        return None

    async def on_tool_error_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
        error: Exception,
    ) -> dict[str, Any] | None:
        """Capture error OUTCOME and convert uncaught tool exceptions into guided recovery."""
        tool_name = getattr(tool, "name", str(tool))
        invocation_id = getattr(tool_context, "invocation_id", "local")
        key = f"{invocation_id}:{tool_name}"
        record = self._in_flight_tools.pop(key, None)
        correlation_id = record.correlation_id if record else uuid.uuid4().hex[:12]
        latency_ms = (
            round((time.perf_counter() - record.started_monotonic) * 1000.0, 2)
            if record
            else 0.0
        )

        if record and record.span is not None:
            record.span.record_exception(error)
            record.span.set_status(Status(StatusCode.ERROR, str(error)))
            record.span.end()

        event_payload = {
            "audit_phase": "OUTCOME",
            "entity_type": "tool",
            "tool_name": tool_name,
            "correlation_id": correlation_id,
            "actual_status": "exception_recovered",
            "latency_ms": latency_ms,
            "outcome_matches_intent": False,
            "error_type": type(error).__name__,
        }
        self._record(event_payload)
        logger.error("tool_error_outcome_captured", **event_payload)

        return ToolErrorResponse(
            error_code="UNEXPECTED_TOOL_EXECUTION_ERROR",
            recoverable=True,
            message=f"Tool '{tool_name}' encountered {type(error).__name__}: {error}",
            recovery_instructions=[
                "Verify all input arguments match the tool's Pydantic schema.",
                "If an external store is unavailable, report the gap transparently and flag unverified figures as BLOCKING.",
            ],
        ).model_dump()
