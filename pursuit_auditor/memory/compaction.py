"""Context bloat management: ADK event compaction, context caching, and sliding windows.

Combines three complementary mechanisms:
1. ADK `EventsCompactionConfig` with `LlmEventSummarizer` on the Fast model tier.
2. ADK `ContextCacheConfig` to cache constitution prompts and policy baselines.
3. Deterministic `TokenBudgetSlidingWindowCompactor` for explicit token-budget
   truncation and archival summarization of long multi-turn audit transcripts.
"""

from __future__ import annotations

from dataclasses import dataclass

from google.adk.agents.context_cache_config import ContextCacheConfig
from google.adk.apps.app import EventsCompactionConfig
from google.adk.apps.llm_event_summarizer import LlmEventSummarizer
from google.adk.models import Gemini

from pursuit_auditor.config.settings import AuditorSettings, get_settings
from pursuit_auditor.observability.logging import get_logger
from pursuit_auditor.observability.pii_redactor import get_pii_redactor

logger = get_logger(__name__)


def build_events_compaction_config(
    settings: AuditorSettings | None = None,
) -> EventsCompactionConfig:
    """Build ADK `EventsCompactionConfig` backed by the fast Gemini summarizer."""
    cfg = settings or get_settings()
    summarizer = LlmEventSummarizer(llm=Gemini(model=cfg.pursuit_auditor_fast_model))
    return EventsCompactionConfig(
        compaction_interval=cfg.compaction_interval_events,
        overlap_size=cfg.compaction_overlap_events,
        summarizer=summarizer,
    )


def build_context_cache_config(
    settings: AuditorSettings | None = None,
) -> ContextCacheConfig:
    """Build ADK `ContextCacheConfig` to cache stable constitution & rubric tokens."""
    cfg = settings or get_settings()
    return ContextCacheConfig(
        min_tokens=cfg.context_cache_min_tokens,
        ttl_seconds=cfg.context_cache_ttl_seconds,
        cache_intervals=10,
    )


@dataclass(slots=True)
class CompactionResult:
    """Result of deterministic token-budget history compaction."""

    retained_turns: list[dict[str, str]]
    compacted_summary: str
    original_turn_count: int
    retained_turn_count: int
    estimated_tokens_before: int
    estimated_tokens_after: int
    was_compacted: bool


class TokenBudgetSlidingWindowCompactor:
    """Deterministic sliding-window history compactor with PII-safe archival summaries."""

    def __init__(self, *, max_token_budget: int = 3000, overlap_turns: int = 3) -> None:
        if max_token_budget < 200:
            raise ValueError("max_token_budget must be >= 200 tokens.")
        if overlap_turns < 1:
            raise ValueError("overlap_turns must be >= 1.")
        self._max_token_budget = max_token_budget
        self._overlap_turns = overlap_turns

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Fast heuristic (~4 chars per token) for deterministic context budgeting."""
        return max(1, (len(text) + 3) // 4)

    def compact(self, turns: list[dict[str, str]]) -> CompactionResult:
        """Compact older turns into a concise scrubbed summary when exceeding budget."""
        tokens_before = sum(
            self.estimate_tokens(f"{t.get('role', '')}: {t.get('content', '')}") for t in turns
        )
        if tokens_before <= self._max_token_budget or len(turns) <= self._overlap_turns:
            return CompactionResult(
                retained_turns=list(turns),
                compacted_summary="",
                original_turn_count=len(turns),
                retained_turn_count=len(turns),
                estimated_tokens_before=tokens_before,
                estimated_tokens_after=tokens_before,
                was_compacted=False,
            )

        redactor = get_pii_redactor()
        # Keep the most recent `overlap_turns` verbatim; condense everything older.
        split_idx = max(1, len(turns) - self._overlap_turns)
        older_turns = turns[:split_idx]
        recent_turns = list(turns[split_idx:])

        highlights: list[str] = []
        for idx, turn in enumerate(older_turns, start=1):
            role = turn.get("role", "unknown")
            clean_text = redactor.redact_text(turn.get("content", "")).redacted_text
            snippet = " ".join(clean_text.split())[:140]
            highlights.append(f"- Turn {idx} ({role}): {snippet}")

        summary_text = (
            f"[Compacted Audit History Summary — {len(older_turns)} earlier turns]\n"
            + "\n".join(highlights)
        )
        summary_turn = {"role": "system", "content": summary_text}
        retained = [summary_turn, *recent_turns]
        tokens_after = sum(
            self.estimate_tokens(f"{t.get('role', '')}: {t.get('content', '')}") for t in retained
        )

        logger.info(
            "history_compacted",
            original_turns=len(turns),
            retained_turns=len(retained),
            tokens_before=tokens_before,
            tokens_after=tokens_after,
        )
        return CompactionResult(
            retained_turns=retained,
            compacted_summary=summary_text,
            original_turn_count=len(turns),
            retained_turn_count=len(retained),
            estimated_tokens_before=tokens_before,
            estimated_tokens_after=tokens_after,
            was_compacted=True,
        )
