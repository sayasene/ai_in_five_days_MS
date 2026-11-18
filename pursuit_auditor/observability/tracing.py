"""OpenTelemetry distributed tracing setup for Pursuit Deliverable Auditor.

Links parent-child spans across request ingress, multi-agent delegation, tool
execution, PII redaction, and asynchronous memory consolidation, exporting to
Google Cloud Trace in production or an in-memory/console exporter locally.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Span, Status, StatusCode, Tracer

_tracer_provider: TracerProvider | None = None
_memory_exporter: InMemorySpanExporter | None = None


def configure_tracing(
    *,
    service_name: str | None = None,
    exporter_mode: str | None = None,
    custom_exporter: SpanExporter | None = None,
) -> TracerProvider:
    """Configure an OpenTelemetry `TracerProvider` with the selected span exporter.

    Args:
        service_name: Logical OTel `service.name` resource attribute.
        exporter_mode: One of `"gcp_trace"`, `"console"`, `"memory"`, or `"none"`.
        custom_exporter: Optional explicit `SpanExporter` (useful in tests).

    Returns:
        The configured `TracerProvider`.
    """
    global _tracer_provider, _memory_exporter

    resolved_service: str = service_name or os.getenv("OTEL_SERVICE_NAME") or "pursuit-auditor-agent"
    resolved_mode: str = exporter_mode or os.getenv("OTEL_EXPORTER_MODE") or "none"

    resource = Resource.create(
        {
            "service.name": resolved_service,
            "service.version": "1.0.0",
            "deployment.environment": os.getenv("ENVIRONMENT") or "local",
        }
    )
    provider = TracerProvider(resource=resource)

    if custom_exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(custom_exporter))
    elif resolved_mode == "memory":
        _memory_exporter = InMemorySpanExporter()
        provider.add_span_processor(SimpleSpanProcessor(_memory_exporter))
    elif resolved_mode == "console":
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    elif resolved_mode == "gcp_trace":
        try:
            from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter

            project_id = os.getenv("GOOGLE_CLOUD_PROJECT") or None
            provider.add_span_processor(
                BatchSpanProcessor(CloudTraceSpanExporter(project_id=project_id))
            )
        except ImportError:
            provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))

    _tracer_provider = provider
    trace.set_tracer_provider(provider)
    return provider


def get_in_memory_span_exporter() -> InMemorySpanExporter | None:
    """Return the active `InMemorySpanExporter` when `exporter_mode='memory'`."""
    return _memory_exporter


def get_tracer(name: str = "pursuit_auditor") -> Tracer:
    """Return an OpenTelemetry tracer scoped to `name`."""
    if _tracer_provider is not None:
        return _tracer_provider.get_tracer(name)
    return trace.get_tracer(name)


def get_current_trace_context() -> dict[str, str]:
    """Extract W3C trace_id and span_id from the active OpenTelemetry span."""
    span = trace.get_current_span()
    ctx = span.get_span_context() if span else None
    if not ctx or not ctx.is_valid:
        return {}
    trace_id = format(ctx.trace_id, "032x")
    span_id = format(ctx.span_id, "016x")
    result = {"trace_id": trace_id, "span_id": span_id}
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    if project_id:
        result["logging.googleapis.com/trace"] = f"projects/{project_id}/traces/{trace_id}"
        result["logging.googleapis.com/spanId"] = span_id
    return result


@contextmanager
def traced_span(
    span_name: str,
    attributes: dict[str, Any] | None = None,
    tracer_name: str = "pursuit_auditor",
) -> Iterator[Span]:
    """Context manager creating a linked OpenTelemetry span with safe attributes."""
    tracer = get_tracer(tracer_name)
    with tracer.start_as_current_span(span_name) as span:
        if attributes:
            for key, value in attributes.items():
                if isinstance(value, (str, bool, int, float)):
                    span.set_attribute(key, value)
                elif value is not None:
                    span.set_attribute(key, str(value))
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise
