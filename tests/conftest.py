"""Shared pytest fixtures for Pursuit Deliverable Auditor test & eval suites."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from pursuit_auditor.observability.tracing import configure_tracing


@dataclass
class FakeToolConfirmation:
    """Minimal ADK ToolConfirmation double for HITL unit tests."""

    confirmed: bool
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class FakeToolContext:
    """Minimal ADK ToolContext double supporting state and HITL `request_confirmation`."""

    state: dict[str, Any] = field(default_factory=dict)
    invocation_id: str = "inv-test-001"
    agent_name: str = "pursuit_audit_coordinator"
    tool_confirmation: FakeToolConfirmation | None = None
    requested_confirmations: list[dict[str, Any]] = field(default_factory=list)

    def request_confirmation(self, *, hint: str, payload: dict[str, Any] | None = None) -> None:
        self.requested_confirmations.append({"hint": hint, "payload": payload or {}})


@pytest.fixture
def span_exporter() -> InMemorySpanExporter:
    """Provide an isolated OpenTelemetry InMemorySpanExporter for trace assertions."""
    exporter = InMemorySpanExporter()
    configure_tracing(service_name="pursuit-auditor-test", custom_exporter=exporter)
    return exporter


@pytest.fixture
def tmp_precedent_db(tmp_path: Path) -> Path:
    """Temporary SQLite path for isolated vector precedent store tests."""
    return tmp_path / "precedents_test.db"
