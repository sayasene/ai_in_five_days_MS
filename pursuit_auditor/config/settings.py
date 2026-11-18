"""Typed application configuration for Pursuit Deliverable Auditor."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PACKAGE_DIR / "data"


class AuditorSettings(BaseSettings):
    """Runtime settings loaded from environment variables (never hardcoded secrets)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Core app identity (matches package directory name for ADK App discovery)
    app_name: str = Field(default="pursuit_auditor")
    environment: Literal["local", "staging", "prod"] = Field(default="local")

    # GCP / Vertex AI settings
    google_genai_use_vertexai: bool = Field(default=False)
    google_cloud_project: str = Field(default="")
    google_cloud_location: str = Field(default="us-central1")

    # Secret Manager configuration
    enable_secret_manager: bool = Field(default=False)
    gemini_api_key_secret_name: str = Field(default="pursuit-auditor-gemini-api-key")
    session_db_url_secret_name: str = Field(default="pursuit-auditor-session-db-url")

    # Strategic model routing tiers
    pursuit_auditor_fast_model: str = Field(default="gemini-2.5-flash")
    pursuit_auditor_reasoning_model: str = Field(default="gemini-2.5-pro")

    # Persistent session & precedent stores
    session_backend: Literal["sqlite", "cloud_sql", "vertex_ai", "in_memory"] = Field(
        default="sqlite"
    )
    session_db_url: str = Field(
        default="sqlite+aiosqlite:///./data/pursuit_auditor_sessions.db"
    )
    precedent_db_path: str = Field(default="./data/pursuit_auditor_precedents.db")
    vertex_ai_search_datastore_id: str = Field(default="")

    # Async memory consolidation
    memory_backend: Literal["in_memory", "vertex_ai"] = Field(default="in_memory")
    agent_engine_id: str = Field(default="")

    # Context compaction & caching
    compaction_interval_events: int = Field(default=12, ge=2)
    compaction_overlap_events: int = Field(default=3, ge=0)
    context_cache_min_tokens: int = Field(default=2048, ge=0)
    context_cache_ttl_seconds: int = Field(default=1800, ge=60)
    context_filter_keep_invocations: int = Field(default=8, ge=1)

    # Guardrails & PII Redaction
    enable_model_armor: bool = Field(default=False)
    model_armor_template_id: str = Field(default="")
    enable_cloud_dlp: bool = Field(default=False)
    dlp_deidentify_template_id: str = Field(default="")

    # Observability & OpenTelemetry
    log_level: str = Field(default="INFO")
    otel_service_name: str = Field(default="pursuit-auditor-agent")
    otel_exporter_mode: Literal["none", "console", "gcp_trace", "otlp", "memory"] = Field(
        default="none"
    )
    otel_instrumentation_genai_capture_message_content: str = Field(default="NO_CONTENT")

    # Audit domain thresholds
    max_self_eval_iterations: int = Field(default=2, ge=1, le=3)
    hitl_deal_value_threshold_usd: float = Field(default=5_000_000.0, ge=0)
    max_table_cell_words: int = Field(default=15, ge=5)
    min_slide_font_pt: float = Field(default=10.0, ge=6.0)
    max_slide_font_pt: float = Field(default=24.0, ge=12.0)

    # Synthetic data fixtures
    baselines_file: Path = Field(default=DATA_DIR / "synthetic_baselines.json")
    precedents_file: Path = Field(default=DATA_DIR / "synthetic_precedents.json")


@lru_cache(maxsize=1)
def get_settings() -> AuditorSettings:
    """Return cached runtime settings."""
    return AuditorSettings()
