"""Context compaction, persistent session state, vector precedents, and async memory."""

from .async_memory import AsyncMemoryConsolidator, build_memory_service, get_memory_consolidator
from .compaction import (
    TokenBudgetSlidingWindowCompactor,
    build_context_cache_config,
    build_events_compaction_config,
)
from .session_store import build_session_service
from .vector_store import (
    AuditPrecedentVectorStore,
    build_vertex_ai_search_tool,
    get_precedent_store,
)

__all__ = [
    "AsyncMemoryConsolidator",
    "AuditPrecedentVectorStore",
    "TokenBudgetSlidingWindowCompactor",
    "build_context_cache_config",
    "build_events_compaction_config",
    "build_memory_service",
    "build_session_service",
    "build_vertex_ai_search_tool",
    "get_memory_consolidator",
    "get_precedent_store",
]
