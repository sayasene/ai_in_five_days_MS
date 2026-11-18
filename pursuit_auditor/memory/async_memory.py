"""Asynchronous, non-blocking background memory consolidation pipeline.

Expensive memory extraction, PII scrubbing, ADK Memory Bank persistence, and
precedent vector indexing execute on a background `asyncio.Task` worker queue so
user turns return immediately without blocking on memory writes.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
from dataclasses import dataclass
from typing import Any

from google.adk.memory import BaseMemoryService, InMemoryMemoryService

from pursuit_auditor.config.settings import AuditorSettings, get_settings
from pursuit_auditor.memory.vector_store import AuditPrecedentVectorStore, get_precedent_store
from pursuit_auditor.observability.logging import get_logger
from pursuit_auditor.observability.pii_redactor import get_pii_redactor
from pursuit_auditor.observability.tracing import traced_span

logger = get_logger(__name__)


def build_memory_service(settings: AuditorSettings | None = None) -> BaseMemoryService:
    """Build ADK MemoryService (`VertexAiMemoryBankService` in GCP or `InMemoryMemoryService`)."""
    cfg = settings or get_settings()
    if cfg.memory_backend == "vertex_ai" and cfg.google_cloud_project and cfg.agent_engine_id:
        from google.adk.memory import VertexAiMemoryBankService

        logger.info("memory_service_initialized", backend="vertex_ai_memory_bank")
        return VertexAiMemoryBankService(
            project=cfg.google_cloud_project,
            location=cfg.google_cloud_location,
            agent_engine_id=cfg.agent_engine_id,
        )
    logger.info("memory_service_initialized", backend="in_memory")
    return InMemoryMemoryService()


@dataclass(slots=True)
class MemoryConsolidationJob:
    """Unit of background memory work queued after an audit turn completes."""

    session_id: str
    deal_id: str
    deliverable_type: str
    summary_text: str
    gate: str = "Gate 4"
    session_obj: Any = None


class AsyncMemoryConsolidator:
    """Non-blocking asyncio queue + worker for background PII-scrubbed memory consolidation."""

    def __init__(
        self,
        *,
        memory_service: BaseMemoryService | None = None,
        precedent_store: AuditPrecedentVectorStore | None = None,
    ) -> None:
        self._memory_service = memory_service or build_memory_service()
        self._precedent_store = precedent_store or get_precedent_store()
        self._queue: asyncio.Queue[MemoryConsolidationJob] = asyncio.Queue()
        self._worker_task: asyncio.Task[None] | None = None
        self.completed_jobs: int = 0
        self.failed_jobs: int = 0

    def _ensure_worker_started(self) -> None:
        """Lazily spawn the background asyncio consumer inside the running event loop."""
        if self._worker_task is None or self._worker_task.done():
            loop = asyncio.get_running_loop()
            self._worker_task = loop.create_task(
                self._worker_loop(), name="pursuit-auditor-memory-worker"
            )

    def enqueue(self, job: MemoryConsolidationJob) -> None:
        """Enqueue a consolidation job without blocking the calling agent turn."""
        self._ensure_worker_started()
        self._queue.put_nowait(job)
        logger.info(
            "async_memory_job_enqueued",
            session_id=job.session_id,
            deal_id=job.deal_id,
            queue_depth=self._queue.qsize(),
        )

    async def _process_job(self, job: MemoryConsolidationJob) -> None:
        """Scrub PII, persist to ADK MemoryService, and index precedent asynchronously."""
        redactor = get_pii_redactor()
        with traced_span(
            "audit.memory.consolidate_async",
            attributes={
                "session.id": job.session_id,
                "deal.id": job.deal_id,
                "audit.gate": job.gate,
            },
        ) as span:
            scrub_report = redactor.redact_text(job.summary_text)
            span.set_attribute("pii.redactions", scrub_report.redaction_count)

            # 1. Persist full ADK session into Memory Bank / MemoryService if available.
            if job.session_obj is not None:
                await self._memory_service.add_session_to_memory(job.session_obj)

            # 2. Persist consolidated, PII-scrubbed audit insight into vector precedent store.
            digest = hashlib.sha256(
                f"{job.session_id}:{job.deal_id}:{scrub_report.redacted_text[:120]}".encode()
            ).hexdigest()[:10]
            precedent_id = f"LEARNED-{digest.upper()}"
            await asyncio.to_thread(
                self._precedent_store.upsert_precedent,
                precedent_id=precedent_id,
                account_sector="Enterprise Cloud Consulting",
                deliverable_type=job.deliverable_type,
                gate=job.gate,
                pattern_title=f"Consolidated Audit Memory ({job.deal_id})",
                summary=scrub_report.redacted_text[:500],
                resolution="Captured asynchronously via post-audit memory consolidation.",
            )
            self.completed_jobs += 1
            logger.info(
                "async_memory_job_completed",
                session_id=job.session_id,
                precedent_id=precedent_id,
                pii_redactions=scrub_report.redaction_count,
            )

    async def _worker_loop(self) -> None:
        """Background consumer draining queued consolidation jobs."""
        while True:
            job = await self._queue.get()
            try:
                await self._process_job(job)
            except Exception as exc:  # noqa: BLE001
                self.failed_jobs += 1
                logger.error(
                    "async_memory_job_failed",
                    session_id=job.session_id,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
            finally:
                self._queue.task_done()

    async def flush(self, timeout_seconds: float = 10.0) -> None:
        """Wait for all queued background memory jobs to complete (used in shutdown/tests)."""
        await asyncio.wait_for(self._queue.join(), timeout=timeout_seconds)

    async def shutdown(self) -> None:
        """Gracefully drain pending jobs and cancel the background worker task."""
        if not self._queue.empty():
            await self.flush()
        if self._worker_task is not None and not self._worker_task.done():
            self._worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker_task


_default_consolidator: AsyncMemoryConsolidator | None = None


def get_memory_consolidator(
    settings: AuditorSettings | None = None,
) -> AsyncMemoryConsolidator:
    """Return process-wide `AsyncMemoryConsolidator`."""
    global _default_consolidator
    if _default_consolidator is None:
        cfg = settings or get_settings()
        _default_consolidator = AsyncMemoryConsolidator(
            memory_service=build_memory_service(cfg),
            precedent_store=get_precedent_store(cfg),
        )
    return _default_consolidator
