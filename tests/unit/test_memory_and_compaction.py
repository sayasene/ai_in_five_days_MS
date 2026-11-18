"""Unit tests for context compaction, persistent sessions, vector store, and async memory."""

from __future__ import annotations

from pathlib import Path

import pytest
from google.adk.memory import InMemoryMemoryService

from pursuit_auditor.config.settings import AuditorSettings
from pursuit_auditor.memory import (
    AsyncMemoryConsolidator,
    AuditPrecedentVectorStore,
    TokenBudgetSlidingWindowCompactor,
    build_context_cache_config,
    build_events_compaction_config,
    build_session_service,
)
from pursuit_auditor.memory.async_memory import MemoryConsolidationJob


def test_adk_compaction_and_context_cache_configs_initialize() -> None:
    compaction_cfg = build_events_compaction_config()
    cache_cfg = build_context_cache_config()
    assert compaction_cfg.compaction_interval >= 2
    assert compaction_cfg.overlap_size >= 0
    assert compaction_cfg.summarizer is not None
    assert cache_cfg.min_tokens == 2048
    assert cache_cfg.ttl_seconds == 1800


def test_sliding_window_compactor_trims_long_histories_and_redacts_pii() -> None:
    compactor = TokenBudgetSlidingWindowCompactor(max_token_budget=250, overlap_turns=2)
    turns = [
        {
            "role": "user" if i % 2 == 0 else "model",
            "content": (
                f"Turn {i}: Reviewed Northstar Retail funding table with contact "
                f"user{i}@example.com and reconciled GSF/PSF workstream totals across 18 months. "
                * 3
            ),
        }
        for i in range(1, 9)
    ]
    result = compactor.compact(turns)
    assert result.was_compacted is True
    assert result.retained_turn_count == 3  # 1 summary turn + 2 overlap turns
    assert result.estimated_tokens_after < result.estimated_tokens_before
    assert "user1@example.com" not in result.compacted_summary
    assert "[REDACTED:EMAIL]" in result.compacted_summary


def test_vector_precedent_store_persists_across_restarts_and_redacts_pii(
    tmp_precedent_db: Path,
) -> None:
    seed = Path(__file__).resolve().parents[2] / "pursuit_auditor" / "data" / "synthetic_precedents.json"
    store1 = AuditPrecedentVectorStore(db_path=tmp_precedent_db, seed_file=seed)
    assert store1.count() == 5

    store1.upsert_precedent(
        precedent_id="PREC-CUSTOM-99",
        account_sector="Retail",
        deliverable_type="services_strategy_deck",
        gate="Gate 1",
        pattern_title="Synthetic Chevron Staffing Mismatch",
        summary="Flagged by reviewer@example.com when deep-dive chevrons summed to 24 months.",
        resolution="Reconciled staffing chevrons to 18-month executive summary.",
    )

    # Re-open SQLite store from disk to verify persistence across process restarts
    store2 = AuditPrecedentVectorStore(db_path=tmp_precedent_db)
    assert store2.count() == 6
    matches = store2.search_precedents(
        query="deep-dive chevrons staffing mismatch 24 months",
        gate_filter="Gate 1",
        top_k=3,
    )
    custom = [m for m in matches if m.precedent_id == "PREC-CUSTOM-99"]
    assert custom
    assert "reviewer@example.com" not in custom[0].summary
    assert "[REDACTED:EMAIL]" in custom[0].summary


@pytest.mark.asyncio
async def test_persistent_database_session_service_survives_service_reload(
    tmp_path: Path,
) -> None:
    db_file = tmp_path / "adk_sessions.db"
    settings = AuditorSettings(
        session_backend="sqlite",
        session_db_url=f"sqlite+aiosqlite:///{db_file}",
    )
    service1 = build_session_service(settings)
    try:
        created = await service1.create_session(
            app_name="pursuit_auditor",
            user_id="pursuit-lead-1",
            session_id="sess-persist-001",
            state={"active_deal_id": "OPP-SYNTH-2026-001"},
        )
        assert created.id == "sess-persist-001"
    finally:
        if hasattr(service1, "db_engine"):
            await service1.db_engine.dispose()

    # Reconstruct a brand-new DatabaseSessionService pointing at the same SQLite DB
    service2 = build_session_service(settings)
    try:
        reloaded = await service2.get_session(
            app_name="pursuit_auditor",
            user_id="pursuit-lead-1",
            session_id="sess-persist-001",
        )
        assert reloaded is not None
        assert reloaded.state["active_deal_id"] == "OPP-SYNTH-2026-001"
    finally:
        if hasattr(service2, "db_engine"):
            await service2.db_engine.dispose()


@pytest.mark.asyncio
async def test_async_memory_consolidator_runs_in_background_and_scrubs_pii(
    tmp_precedent_db: Path,
) -> None:
    store = AuditPrecedentVectorStore(db_path=tmp_precedent_db)
    mem_service = InMemoryMemoryService()
    consolidator = AsyncMemoryConsolidator(
        memory_service=mem_service,
        precedent_store=store,
    )
    consolidator.enqueue(
        MemoryConsolidationJob(
            session_id="sess-async-01",
            deal_id="OPP-SYNTH-2026-002",
            deliverable_type="partner_proposal",
            summary_text="Flagged partner leak raised by director@example.com on Slide 4.",
            gate="Gate 2",
        )
    )
    await consolidator.flush(timeout_seconds=5.0)
    await consolidator.shutdown()

    assert consolidator.completed_jobs == 1
    assert consolidator.failed_jobs == 0
    assert store.count() == 1
    matches = store.search_precedents(query="partner leak Slide 4", top_k=1)
    assert matches
    assert "director@example.com" not in matches[0].summary
    assert "[REDACTED:EMAIL]" in matches[0].summary
