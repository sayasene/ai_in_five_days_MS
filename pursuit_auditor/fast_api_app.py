"""Production Cloud Run FastAPI entrypoint for Pursuit Deliverable Auditor.

Wraps the ADK `get_fast_api_app` server with:
- Persistent session backend URI (Cloud SQL PostgreSQL in prod, SQLite locally)
- Native OpenTelemetry export to Google Cloud Trace (`otel_to_cloud=True` in GCP)
- Graceful shutdown lifespan draining queued async memory consolidation tasks
- Kubernetes / Cloud Run `/healthz` readiness endpoint
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from google.adk.cli.fast_api import get_fast_api_app

from pursuit_auditor.config.secrets import get_secret_provider
from pursuit_auditor.config.settings import get_settings
from pursuit_auditor.memory.async_memory import get_memory_consolidator
from pursuit_auditor.observability.logging import configure_structured_logging, get_logger

settings = get_settings()
configure_structured_logging(level=settings.log_level)
logger = get_logger(__name__)


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Drain background async memory tasks cleanly before container termination."""
    logger.info("fastapi_service_starting", environment=settings.environment)
    try:
        yield
    finally:
        consolidator = get_memory_consolidator(settings)
        await consolidator.shutdown()
        logger.info(
            "fastapi_service_stopped",
            completed_memory_jobs=consolidator.completed_jobs,
            failed_memory_jobs=consolidator.failed_jobs,
        )


def _resolve_session_service_uri() -> str | None:
    if settings.session_backend == "in_memory":
        return None
    if settings.session_backend == "cloud_sql":
        provider = get_secret_provider(
            project_id=settings.google_cloud_project,
            use_secret_manager=settings.enable_secret_manager,
        )
        secret_uri = provider.get_secret(
            secret_id=settings.session_db_url_secret_name,
            env_fallback="SESSION_DB_URL",
            required=False,
        )
        return secret_uri or settings.session_db_url
    return settings.session_db_url


AGENTS_DIR = str(Path(__file__).resolve().parent.parent)

fastapi_app: FastAPI = get_fast_api_app(
    agents_dir=AGENTS_DIR,
    session_service_uri=_resolve_session_service_uri(),
    web=(settings.environment == "local"),
    otel_to_cloud=(settings.otel_exporter_mode == "gcp_trace"),
    lifespan=_lifespan,
    auto_create_session=True,
)


@fastapi_app.get("/healthz", tags=["system"])
async def healthz() -> dict[str, str]:
    """Liveness and readiness probe for Cloud Run."""
    return {
        "status": "ok",
        "service": settings.otel_service_name,
        "environment": settings.environment,
    }
