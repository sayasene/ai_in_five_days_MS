"""Persistent ADK SessionService factory (SQLite, Cloud SQL PostgreSQL, or Vertex AI)."""

from __future__ import annotations

from pathlib import Path

from google.adk.sessions import (
    BaseSessionService,
    DatabaseSessionService,
    InMemorySessionService,
)

from pursuit_auditor.config.secrets import get_secret_provider
from pursuit_auditor.config.settings import AuditorSettings, get_settings
from pursuit_auditor.observability.logging import get_logger

logger = get_logger(__name__)


def _ensure_sqlite_parent_dir(db_url: str) -> None:
    """Create parent directory for file-backed SQLite database URLs."""
    for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
        if db_url.startswith(prefix):
            raw_path = db_url[len(prefix) :]
            if raw_path and raw_path != ":memory:":
                Path(raw_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


def build_session_service(settings: AuditorSettings | None = None) -> BaseSessionService:
    """Instantiate the configured ADK SessionService for multi-turn persistence.

    Supports:
    - `sqlite`: Persistent local SQLite via ADK `DatabaseSessionService` (default).
    - `cloud_sql`: Production Cloud SQL PostgreSQL via ADK `DatabaseSessionService`,
      with connection URI fetched securely via Google Cloud Secret Manager.
    - `vertex_ai`: Managed `VertexAiSessionService` on Vertex AI Agent Engine.
    - `in_memory`: Ephemeral `InMemorySessionService` for isolated unit tests.
    """
    cfg = settings or get_settings()

    if cfg.session_backend == "in_memory":
        logger.info("session_service_initialized", backend="in_memory")
        return InMemorySessionService()

    if cfg.session_backend == "vertex_ai":
        from google.adk.sessions import VertexAiSessionService

        logger.info(
            "session_service_initialized",
            backend="vertex_ai",
            project=cfg.google_cloud_project,
            location=cfg.google_cloud_location,
        )
        return VertexAiSessionService(
            project=cfg.google_cloud_project or None,
            location=cfg.google_cloud_location or None,
        )

    db_url = cfg.session_db_url
    if cfg.session_backend == "cloud_sql":
        secret_provider = get_secret_provider(
            project_id=cfg.google_cloud_project,
            use_secret_manager=cfg.enable_secret_manager,
        )
        resolved_secret_url = secret_provider.get_secret(
            secret_id=cfg.session_db_url_secret_name,
            env_fallback="SESSION_DB_URL",
            required=False,
        )
        if resolved_secret_url:
            db_url = resolved_secret_url

    _ensure_sqlite_parent_dir(db_url)
    logger.info("session_service_initialized", backend=cfg.session_backend)
    return DatabaseSessionService(db_url=db_url)
