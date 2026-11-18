"""Active PII and sensitive-commercial-token redaction pipeline.

Integrates Google Cloud Sensitive Data Protection (Cloud DLP API) when enabled
in GCP environments and always applies a deterministic local regex redaction
layer as defense-in-depth before structured logs or persistent memory writes.
"""

from __future__ import annotations

import importlib
import os
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

_PII_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "EMAIL_ADDRESS",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        "[REDACTED:EMAIL]",
    ),
    (
        "US_SOCIAL_SECURITY_NUMBER",
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        "[REDACTED:SSN]",
    ),
    (
        "PHONE_NUMBER",
        re.compile(r"(?<!\w)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b"),
        "[REDACTED:PHONE]",
    ),
    (
        "CREDIT_CARD_NUMBER",
        re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b"),
        "[REDACTED:CREDIT_CARD]",
    ),
    (
        "API_KEY_OR_TOKEN",
        re.compile(
            r"\b(?:AIza[0-9A-Za-z\-_]{20,}|sk-[0-9A-Za-z]{16,}|ghp_[0-9A-Za-z]{20,}|ya29\.[0-9A-Za-z\-_]+)\b"
        ),
        "[REDACTED:SECRET_TOKEN]",
    ),
    (
        "BEARER_CREDENTIAL",
        re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/]+=*"),
        "Bearer [REDACTED:TOKEN]",
    ),
    (
        "CRM_CONTACT_ID",
        re.compile(r"\b003[A-Za-z0-9]{12,15}\b"),
        "[REDACTED:CRM_CONTACT_ID]",
    ),
)

_SENSITIVE_KEY_SUBSTRINGS: tuple[str, ...] = (
    "api_key",
    "apikey",
    "secret",
    "password",
    "passwd",
    "token",
    "authorization",
    "private_key",
    "ssn",
)


class CloudDlpBackend(Protocol):
    """Protocol for Google Cloud DLP de-identification clients."""

    def deidentify_text(self, text: str) -> tuple[str, list[str]]:
        """De-identify PII in text and return `(redacted_text, info_types_detected)`."""
        ...


class GoogleCloudDlpBackend:
    """Production Google Cloud Sensitive Data Protection (`google-cloud-dlp`) adapter."""

    DEFAULT_INFO_TYPES = (
        "EMAIL_ADDRESS",
        "PHONE_NUMBER",
        "US_SOCIAL_SECURITY_NUMBER",
        "CREDIT_CARD_NUMBER",
        "GCP_API_KEY",
        "AUTH_TOKEN",
    )

    def __init__(self, project_id: str, deidentify_template_id: str = "") -> None:
        self._project_id = project_id
        self._template_id = deidentify_template_id
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            dlp_v2 = importlib.import_module("google.cloud.dlp_v2")
            self._client = dlp_v2.DlpServiceClient()
        return self._client

    def deidentify_text(self, text: str) -> tuple[str, list[str]]:
        client = self._get_client()
        parent = f"projects/{self._project_id}/locations/global"
        request: dict[str, Any] = {
            "parent": parent,
            "item": {"value": text},
            "inspect_config": {
                "info_types": [{"name": itype} for itype in self.DEFAULT_INFO_TYPES]
            },
        }
        if self._template_id:
            request["deidentify_template_name"] = self._template_id
        else:
            request["deidentify_config"] = {
                "info_type_transformations": {
                    "transformations": [
                        {
                            "primitive_transformation": {
                                "replace_with_info_type_config": {}
                            }
                        }
                    ]
                }
            }
        response = client.deidentify_content(request=request)
        summaries = response.overview.transformation_summaries if response.overview else []
        detected = [
            s.info_type.name for s in summaries if getattr(s, "info_type", None) and s.info_type.name
        ]
        return response.item.value, detected


@dataclass(slots=True)
class RedactionReport:
    """Result returned by `PIIRedactor.redact_text`."""

    redacted_text: str
    redaction_count: int = 0
    detected_categories: list[str] = field(default_factory=list)
    used_cloud_dlp: bool = False


class PIIRedactor:
    """Scrubs PII and credentials from text and nested dicts prior to logging or storage."""

    def __init__(
        self,
        *,
        enable_cloud_dlp: bool = False,
        project_id: str = "",
        deidentify_template_id: str = "",
        dlp_backend: CloudDlpBackend | None = None,
    ) -> None:
        self._enable_cloud_dlp = enable_cloud_dlp
        self._project_id = project_id
        self._template_id = deidentify_template_id
        self._dlp_backend = dlp_backend

    def _get_dlp_backend(self) -> CloudDlpBackend | None:
        if not self._enable_cloud_dlp:
            return None
        if self._dlp_backend is not None:
            return self._dlp_backend
        if self._project_id and self._project_id != "your-gcp-project-id":
            self._dlp_backend = GoogleCloudDlpBackend(
                project_id=self._project_id,
                deidentify_template_id=self._template_id,
            )
            return self._dlp_backend
        return None

    def redact_text(self, text: str) -> RedactionReport:
        """Scrub PII and secret tokens from a plain string."""
        if not text:
            return RedactionReport(redacted_text=text)

        redacted = text
        categories: list[str] = []
        total_replacements = 0
        used_cloud_dlp = False

        backend = self._get_dlp_backend()
        if backend is not None:
            try:
                redacted, dlp_types = backend.deidentify_text(redacted)
                used_cloud_dlp = True
                for item in dlp_types:
                    if item not in categories:
                        categories.append(item)
                if dlp_types:
                    total_replacements += len(dlp_types)
            except Exception:  # noqa: BLE001
                # Always fall back to deterministic local scrubbing — never fail open.
                used_cloud_dlp = False

        for category, pattern, replacement in _PII_PATTERNS:
            redacted, count = pattern.subn(replacement, redacted)
            if count > 0:
                total_replacements += count
                if category not in categories:
                    categories.append(category)

        return RedactionReport(
            redacted_text=redacted,
            redaction_count=total_replacements,
            detected_categories=categories,
            used_cloud_dlp=used_cloud_dlp,
        )

    def redact_payload(self, value: Any) -> Any:
        """Recursively redact sensitive keys and PII strings inside dicts/lists."""
        if isinstance(value, str):
            return self.redact_text(value).redacted_text
        if isinstance(value, dict):
            sanitized: dict[str, Any] = {}
            for key, item in value.items():
                key_lower = str(key).lower()
                if any(marker in key_lower for marker in _SENSITIVE_KEY_SUBSTRINGS):
                    sanitized[str(key)] = "[REDACTED:SENSITIVE_FIELD]"
                else:
                    sanitized[str(key)] = self.redact_payload(item)
            return sanitized
        if isinstance(value, list):
            return [self.redact_payload(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.redact_payload(item) for item in value)
        return value


_default_redactor: PIIRedactor | None = None


def get_pii_redactor() -> PIIRedactor:
    """Return process-wide PII redactor configured from environment flags."""
    global _default_redactor
    if _default_redactor is None:
        _default_redactor = PIIRedactor(
            enable_cloud_dlp=os.getenv("ENABLE_CLOUD_DLP", "false").lower() == "true",
            project_id=os.getenv("GOOGLE_CLOUD_PROJECT", ""),
            deidentify_template_id=os.getenv("DLP_DEIDENTIFY_TEMPLATE_ID", ""),
        )
    return _default_redactor
