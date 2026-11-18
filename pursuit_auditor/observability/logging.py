"""Structured JSON logging with automatic OpenTelemetry trace correlation & PII scrubbing.

All runtime logs are emitted as single-line JSON payloads formatted for Google
Cloud Logging (`severity`, `timestamp`, `logging.googleapis.com/trace`,
`span_id`). Bare `print()` statements are prohibited across the codebase.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

from .pii_redactor import get_pii_redactor
from .tracing import get_current_trace_context

_configured = False


def _inject_gcp_severity(
    _logger: Any, method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Map Python log levels to Google Cloud Logging `severity` strings."""
    severity_map = {
        "debug": "DEBUG",
        "info": "INFO",
        "warning": "WARNING",
        "warn": "WARNING",
        "error": "ERROR",
        "critical": "CRITICAL",
        "exception": "ERROR",
    }
    event_dict["severity"] = severity_map.get(method_name.lower(), "INFO")
    event_dict.setdefault("service", os.getenv("OTEL_SERVICE_NAME", "pursuit-auditor-agent"))
    return event_dict


def _inject_trace_context(
    _logger: Any, _method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Attach active OpenTelemetry trace/span identifiers to every JSON log entry."""
    trace_ctx = get_current_trace_context()
    for key, value in trace_ctx.items():
        event_dict.setdefault(key, value)
    return event_dict


def _redact_pii_processor(
    _logger: Any, _method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Scrub PII and secrets from every structured log field before serialization."""
    redactor = get_pii_redactor()
    sanitized: dict[str, Any] = redactor.redact_payload(dict(event_dict))
    return sanitized


class _DynamicStdoutPrintLogger:
    """Writes single-line JSON logs to the active `sys.stdout` without caching stale streams."""

    def msg(self, message: str) -> None:
        sys.stdout.write(f"{message}\n")
        sys.stdout.flush()

    log = debug = info = warn = warning = msg
    fatal = failure = err = error = critical = exception = msg


def _dynamic_logger_factory(*_args: Any) -> _DynamicStdoutPrintLogger:
    return _DynamicStdoutPrintLogger()


def configure_structured_logging(level: str | None = None, force: bool = False) -> None:
    """Configure `structlog` and stdlib logging to output scrubbed JSON lines."""
    global _configured
    if _configured and not force:
        return

    resolved_level = (level or os.getenv("LOG_LEVEL") or "INFO").upper()
    numeric_level = getattr(logging, resolved_level, logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=numeric_level,
        force=True,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _inject_gcp_severity,
            _inject_trace_context,
            _redact_pii_processor,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        context_class=dict,
        logger_factory=_dynamic_logger_factory,
        cache_logger_on_first_use=False,
    )
    _configured = True


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a structured JSON logger bound with `logger_name`."""
    configure_structured_logging()
    return structlog.get_logger(logger_name=name)
