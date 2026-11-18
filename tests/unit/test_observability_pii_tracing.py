"""Unit tests for PII redaction (Cloud DLP + regex), OpenTelemetry tracing, and JSON logs."""

from __future__ import annotations

import json

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from pursuit_auditor.observability.logging import configure_structured_logging, get_logger
from pursuit_auditor.observability.pii_redactor import PIIRedactor
from pursuit_auditor.observability.tracing import traced_span


class _StubDlpBackend:
    def deidentify_text(self, text: str) -> tuple[str, list[str]]:
        return (text.replace("Alex Rivera", "[PERSON_NAME]"), ["PERSON_NAME"])


class _FailingDlpBackend:
    def deidentify_text(self, text: str) -> tuple[str, list[str]]:
        raise RuntimeError("Simulated Cloud DLP timeout")


def test_pii_redactor_scrubs_all_sensitive_patterns_and_nested_keys() -> None:
    redactor = PIIRedactor(enable_cloud_dlp=False)
    sample = (
        "Email cio@northstar-retail.example, phone 415-555-0199, SSN 123-45-6789, "
        "token Bearer abc123XYZ== and key AIzaSyDExampleFakeKey1234567890."
    )
    report = redactor.redact_text(sample)
    assert "[REDACTED:EMAIL]" in report.redacted_text
    assert "[REDACTED:PHONE]" in report.redacted_text
    assert "[REDACTED:SSN]" in report.redacted_text
    assert "Bearer [REDACTED:TOKEN]" in report.redacted_text
    assert "[REDACTED:SECRET_TOKEN]" in report.redacted_text
    assert "cio@northstar-retail.example" not in report.redacted_text
    assert "123-45-6789" not in report.redacted_text
    assert report.redaction_count >= 5

    nested = redactor.redact_payload(
        {
            "deal_id": "OPP-SYNTH-2026-001",
            "api_key": "must-never-appear",
            "notes": ["Reach alex@example.com for approval"],
        }
    )
    assert nested["api_key"] == "[REDACTED:SENSITIVE_FIELD]"
    assert nested["notes"][0] == "Reach [REDACTED:EMAIL] for approval"


def test_pii_redactor_integrates_cloud_dlp_and_fails_closed_on_error() -> None:
    dlp_redactor = PIIRedactor(enable_cloud_dlp=True, dlp_backend=_StubDlpBackend())
    res = dlp_redactor.redact_text("Alex Rivera sent alex@example.com")
    assert res.used_cloud_dlp is True
    assert "[PERSON_NAME]" in res.redacted_text
    assert "[REDACTED:EMAIL]" in res.redacted_text

    fallback_redactor = PIIRedactor(enable_cloud_dlp=True, dlp_backend=_FailingDlpBackend())
    res_fb = fallback_redactor.redact_text("Fallback email test@example.com")
    assert res_fb.used_cloud_dlp is False
    assert "[REDACTED:EMAIL]" in res_fb.redacted_text


def test_opentelemetry_links_parent_and_child_spans_and_correlates_json_logs(
    span_exporter: InMemorySpanExporter,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_structured_logging(level="INFO", force=True)
    log = get_logger("tests.observability")

    with (
        traced_span("audit.request", {"deal.id": "OPP-SYNTH-2026-001"}),
        traced_span("audit.gate.gate1", {"audit.gate": "Gate 1"}),
    ):
        log.info("gate1_checked", stakeholder_email="cfo@example.com", passed=True)

    spans = span_exporter.get_finished_spans()
    assert len(spans) == 2
    child_span, parent_span = spans[0], spans[1]
    assert child_span.name == "audit.gate.gate1"
    assert parent_span.name == "audit.request"
    assert child_span.parent is not None
    assert child_span.parent.span_id == parent_span.context.span_id
    assert child_span.context.trace_id == parent_span.context.trace_id

    captured = capsys.readouterr().out.strip().splitlines()
    parsed = json.loads(captured[-1])
    assert parsed["event"] == "gate1_checked"
    assert parsed["severity"] == "INFO"
    assert parsed["stakeholder_email"] == "[REDACTED:EMAIL]"
    assert parsed["trace_id"] == format(parent_span.context.trace_id, "032x")
    assert parsed["span_id"] == format(child_span.context.span_id, "016x")
