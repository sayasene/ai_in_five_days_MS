"""Persistent vector precedent store (SQLite cosine index + Vertex AI Search connector).

Stores historical pursuit audit rulings with normalized vector embeddings so
every audit turn can retrieve precedent decisions across restarts. In GCP
production, pairs cleanly with Vertex AI Search (`VertexAiSearchTool`) or Cloud
SQL `pgvector`.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pursuit_auditor.config.settings import AuditorSettings, get_settings
from pursuit_auditor.observability.logging import get_logger
from pursuit_auditor.observability.pii_redactor import get_pii_redactor
from pursuit_auditor.observability.tracing import traced_span
from pursuit_auditor.schemas import PrecedentMatch

logger = get_logger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]{2,}")
_EMBEDDING_DIM = 128


def _compute_text_embedding(text: str, dim: int = _EMBEDDING_DIM) -> list[float]:
    """Deterministic L2-normalized hashed n-gram embedding for offline/local vector retrieval."""
    vec = [0.0] * dim
    tokens = _TOKEN_RE.findall(text.lower())
    if not tokens:
        return vec

    features = list(tokens)
    for i in range(len(tokens) - 1):
        features.append(f"{tokens[i]}_{tokens[i + 1]}")

    for feature in features:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if (digest[4] & 1) == 0 else -1.0
        vec[bucket] += sign

    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:
        return vec
    return [round(v / norm, 6) for v in vec]


def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Compute cosine similarity between two normalized vectors."""
    if len(vec_a) != len(vec_b):
        return 0.0
    score = sum(a * b for a, b in zip(vec_a, vec_b, strict=True))
    return max(0.0, min(1.0, round(score, 4)))


class AuditPrecedentVectorStore:
    """SQLite-persisted vector index of historical pursuit audit findings and resolutions."""

    def __init__(self, db_path: str | Path, seed_file: Path | None = None) -> None:
        self._db_path = Path(db_path)
        if str(self._db_path) != ":memory:":
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._seed_file = seed_file
        self._init_schema()
        if seed_file and seed_file.exists():
            self._seed_if_empty(seed_file)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_precedents (
                    precedent_id TEXT PRIMARY KEY,
                    account_sector TEXT NOT NULL,
                    deliverable_type TEXT NOT NULL,
                    gate TEXT NOT NULL,
                    pattern_title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    resolution TEXT NOT NULL,
                    embedding_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def _seed_if_empty(self, seed_file: Path) -> None:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM audit_precedents").fetchone()
            if row and row["cnt"] > 0:
                return
        records = json.loads(seed_file.read_text(encoding="utf-8"))
        for record in records:
            self.upsert_precedent(
                precedent_id=record["precedent_id"],
                account_sector=record["account_sector"],
                deliverable_type=record["deliverable_type"],
                gate=record["gate"],
                pattern_title=record["pattern_title"],
                summary=record["summary"],
                resolution=record["resolution"],
            )
        logger.info("precedent_vector_store_seeded", count=len(records), db_path=str(self._db_path))

    def upsert_precedent(
        self,
        *,
        precedent_id: str,
        account_sector: str,
        deliverable_type: str,
        gate: str,
        pattern_title: str,
        summary: str,
        resolution: str,
    ) -> None:
        """Scrub PII, compute embedding, and persist an audit precedent to SQLite."""
        redactor = get_pii_redactor()
        clean_title = redactor.redact_text(pattern_title).redacted_text
        clean_summary = redactor.redact_text(summary).redacted_text
        clean_resolution = redactor.redact_text(resolution).redacted_text

        searchable = f"{gate} {deliverable_type} {account_sector} {clean_title} {clean_summary} {clean_resolution}"
        embedding = _compute_text_embedding(searchable)
        now = datetime.now(UTC).isoformat()

        with (
            traced_span(
                "audit.vector_store.upsert",
                attributes={"precedent.id": precedent_id, "precedent.gate": gate},
            ),
            self._connect() as conn,
        ):
            conn.execute(
                """
                INSERT INTO audit_precedents (
                    precedent_id, account_sector, deliverable_type, gate,
                    pattern_title, summary, resolution, embedding_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(precedent_id) DO UPDATE SET
                    account_sector=excluded.account_sector,
                    deliverable_type=excluded.deliverable_type,
                    gate=excluded.gate,
                    pattern_title=excluded.pattern_title,
                    summary=excluded.summary,
                    resolution=excluded.resolution,
                    embedding_json=excluded.embedding_json,
                    updated_at=excluded.updated_at
                """,
                (
                    precedent_id,
                    account_sector,
                    deliverable_type,
                    gate,
                    clean_title,
                    clean_summary,
                    clean_resolution,
                    json.dumps(embedding),
                    now,
                ),
            )
            conn.commit()

    def search_precedents(
        self,
        *,
        query: str,
        gate_filter: str | None = None,
        top_k: int = 3,
    ) -> list[PrecedentMatch]:
        """Search persistent precedent embeddings using cosine similarity + lexical boost."""
        query_vec = _compute_text_embedding(query)
        query_terms = set(_TOKEN_RE.findall(query.lower()))

        with traced_span(
            "audit.vector_store.search",
            attributes={"search.top_k": top_k, "search.gate_filter": gate_filter or "all"},
        ):
            with self._connect() as conn:
                if gate_filter:
                    rows = conn.execute(
                        "SELECT * FROM audit_precedents WHERE gate = ?", (gate_filter,)
                    ).fetchall()
                else:
                    rows = conn.execute("SELECT * FROM audit_precedents").fetchall()

            scored: list[PrecedentMatch] = []
            for row in rows:
                emb = json.loads(row["embedding_json"])
                cos = _cosine_similarity(query_vec, emb)
                doc_terms = set(
                    _TOKEN_RE.findall(
                        f"{row['pattern_title']} {row['summary']} {row['deliverable_type']}".lower()
                    )
                )
                lexical = (
                    len(query_terms & doc_terms) / max(1, len(query_terms))
                    if query_terms
                    else 0.0
                )
                blended_score = round(min(1.0, 0.7 * cos + 0.3 * lexical), 4)
                scored.append(
                    PrecedentMatch(
                        precedent_id=row["precedent_id"],
                        gate=row["gate"],
                        deliverable_type=row["deliverable_type"],
                        pattern_title=row["pattern_title"],
                        summary=row["summary"],
                        resolution=row["resolution"],
                        similarity_score=blended_score,
                    )
                )

            scored.sort(key=lambda item: item.similarity_score, reverse=True)
            return scored[:top_k]

    def count(self) -> int:
        """Return total number of stored audit precedents."""
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM audit_precedents").fetchone()
            return int(row["cnt"]) if row else 0


def build_vertex_ai_search_tool(settings: AuditorSettings | None = None) -> Any | None:
    """Return an ADK `VertexAiSearchTool` when a GCP enterprise datastore ID is configured."""
    cfg = settings or get_settings()
    if not cfg.vertex_ai_search_datastore_id:
        return None
    from google.adk.tools import VertexAiSearchTool

    logger.info(
        "vertex_ai_search_tool_enabled",
        datastore_configured=True,
    )
    return VertexAiSearchTool(data_store_id=cfg.vertex_ai_search_datastore_id)


_default_store: AuditPrecedentVectorStore | None = None


def get_precedent_store(settings: AuditorSettings | None = None) -> AuditPrecedentVectorStore:
    """Return the process-wide persistent `AuditPrecedentVectorStore`."""
    global _default_store
    if _default_store is None:
        cfg = settings or get_settings()
        _default_store = AuditPrecedentVectorStore(
            db_path=cfg.precedent_db_path,
            seed_file=cfg.baselines_file.parent / "synthetic_precedents.json",
        )
    return _default_store
