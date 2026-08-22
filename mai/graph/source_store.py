from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Iterable


_ALLOWED_SOURCE_KINDS = frozenset(
    {
        "user_message",
        "assistant_message",
        "web_evidence",
        "file_evidence",
        "tool_operation",
        "scratchpad",
    }
)

_SOURCE_RELIABILITY = {
    "user_message": 1.00,
    "web_evidence": 0.82,
    "file_evidence": 0.76,
    "tool_operation": 0.66,
    "scratchpad": 0.58,
    "assistant_message": 0.46,
}

_SOURCE_STABILITY = {
    "user_message": 0.82,
    "web_evidence": 0.58,
    "file_evidence": 0.68,
    "tool_operation": 0.55,
    "scratchpad": 0.52,
    "assistant_message": 0.38,
}


@dataclass(frozen=True, slots=True)
class SourceRecord:
    source_kind: str
    source_key: str
    content: str
    metadata: dict[str, Any]

    def normalized(self) -> "SourceRecord":
        kind = str(self.source_kind).strip()
        key = str(self.source_key).strip()
        content = str(self.content)
        if kind not in _ALLOWED_SOURCE_KINDS:
            raise ValueError(f"unsupported graph source kind: {kind}")
        if not key:
            raise ValueError("graph source key must be non-empty")
        if not content.strip():
            raise ValueError("graph source content must be non-empty")
        return SourceRecord(kind, key, content, dict(self.metadata))


class GraphSourceStore:
    """Durable raw-source storage and structural confidence metadata for graph memory."""

    def __init__(self, db_path: str | Path, *, busy_timeout_ms: int = 5000) -> None:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), timeout=busy_timeout_ms / 1000, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = Lock()
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._create_schema()

    def _create_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS graph_sources (
                source_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                source_kind TEXT NOT NULL,
                source_key TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, turn_id, source_kind, source_key)
            );

            CREATE INDEX IF NOT EXISTS idx_graph_sources_user
            ON graph_sources(user_id, source_id);

            CREATE TABLE IF NOT EXISTS graph_source_links (
                link_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                source_id INTEGER NOT NULL,
                node_id INTEGER,
                edge_id INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CHECK ((node_id IS NOT NULL) != (edge_id IS NOT NULL)),
                FOREIGN KEY(source_id) REFERENCES graph_sources(source_id),
                FOREIGN KEY(node_id) REFERENCES graph_nodes(node_id),
                FOREIGN KEY(edge_id) REFERENCES graph_edges(edge_id)
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_graph_source_link_node_unique
            ON graph_source_links(user_id, source_id, node_id)
            WHERE node_id IS NOT NULL;

            CREATE UNIQUE INDEX IF NOT EXISTS idx_graph_source_link_edge_unique
            ON graph_source_links(user_id, source_id, edge_id)
            WHERE edge_id IS NOT NULL;

            CREATE INDEX IF NOT EXISTS idx_graph_source_links_node
            ON graph_source_links(user_id, node_id);

            CREATE INDEX IF NOT EXISTS idx_graph_source_links_edge
            ON graph_source_links(user_id, edge_id);

            CREATE TABLE IF NOT EXISTS graph_edge_signals (
                user_id TEXT NOT NULL,
                edge_id INTEGER NOT NULL,
                conflict_count INTEGER NOT NULL DEFAULT 0 CHECK (conflict_count >= 0),
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(user_id, edge_id),
                FOREIGN KEY(edge_id) REFERENCES graph_edges(edge_id)
            );
            """
        )
        self._conn.commit()

    @staticmethod
    def _required(value: str, field: str) -> str:
        clean = str(value).strip()
        if not clean:
            raise ValueError(f"{field} must be non-empty")
        return clean

    def ensure_sources_in_connection(
        self,
        conn: sqlite3.Connection,
        *,
        user_id: str,
        turn_id: str,
        records: Iterable[SourceRecord],
    ) -> list[int]:
        resolved_user = self._required(user_id, "user_id")
        resolved_turn = self._required(turn_id, "turn_id")
        source_ids: list[int] = []
        for raw_record in records:
            record = raw_record.normalized()
            encoded_metadata = json.dumps(record.metadata, ensure_ascii=False, sort_keys=True, default=str)
            row = conn.execute(
                """
                SELECT source_id, content, metadata_json
                FROM graph_sources
                WHERE user_id=? AND turn_id=? AND source_kind=? AND source_key=?
                """,
                (resolved_user, resolved_turn, record.source_kind, record.source_key),
            ).fetchone()
            if row is None:
                cursor = conn.execute(
                    """
                    INSERT INTO graph_sources
                        (user_id, turn_id, source_kind, source_key, content, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        resolved_user,
                        resolved_turn,
                        record.source_kind,
                        record.source_key,
                        record.content,
                        encoded_metadata,
                    ),
                )
                source_ids.append(int(cursor.lastrowid))
                continue
            if str(row["content"]) != record.content or str(row["metadata_json"]) != encoded_metadata:
                raise RuntimeError(
                    "stable graph source identity collision with different content: "
                    f"{record.source_kind}:{record.source_key}"
                )
            source_ids.append(int(row["source_id"]))
        return source_ids

    def link_sources_in_connection(
        self,
        conn: sqlite3.Connection,
        *,
        user_id: str,
        turn_id: str,
        source_ids: Iterable[int],
        node_id: int | None = None,
        edge_id: int | None = None,
    ) -> None:
        if (node_id is None) == (edge_id is None):
            raise ValueError("exactly one graph source link target is required")
        for source_id in dict.fromkeys(int(value) for value in source_ids):
            row = conn.execute(
                "SELECT user_id FROM graph_sources WHERE source_id=?",
                (source_id,),
            ).fetchone()
            if row is None or str(row["user_id"]) != user_id:
                raise PermissionError(f"source_id {source_id} is outside user source scope")
            conn.execute(
                """
                INSERT OR IGNORE INTO graph_source_links
                    (user_id, turn_id, source_id, node_id, edge_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, turn_id, source_id, node_id, edge_id),
            )

    def record_edge_conflict_in_connection(
        self,
        conn: sqlite3.Connection,
        *,
        user_id: str,
        edge_id: int,
    ) -> None:
        conn.execute(
            """
            INSERT INTO graph_edge_signals (user_id, edge_id, conflict_count)
            VALUES (?, ?, 1)
            ON CONFLICT(user_id, edge_id)
            DO UPDATE SET
                conflict_count = graph_edge_signals.conflict_count + 1,
                updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, int(edge_id)),
        )

    def _linked_sources(
        self,
        *,
        user_id: str,
        node_id: int | None = None,
        edge_id: int | None = None,
    ) -> list[dict[str, Any]]:
        if (node_id is None) == (edge_id is None):
            raise ValueError("exactly one provenance target is required")
        target_field = "node_id" if node_id is not None else "edge_id"
        target_id = int(node_id if node_id is not None else edge_id)
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT s.source_id, s.turn_id, s.source_kind, s.source_key,
                       s.metadata_json, s.created_at
                FROM graph_source_links l
                JOIN graph_sources s ON s.source_id=l.source_id AND s.user_id=l.user_id
                WHERE l.user_id=? AND l.{target_field}=?
                ORDER BY l.link_id
                """,
                (user_id, target_id),
            ).fetchall()
        sources: list[dict[str, Any]] = []
        for row in rows:
            kind = str(row["source_kind"])
            metadata = json.loads(str(row["metadata_json"]))
            if not isinstance(metadata, dict):
                raise ValueError("graph source metadata must decode to an object")
            sources.append(
                {
                    "source_id": int(row["source_id"]),
                    "turn_id": str(row["turn_id"]),
                    "source_kind": kind,
                    "source_key": str(row["source_key"]),
                    "source_reliability": _SOURCE_RELIABILITY[kind],
                    "stability": _SOURCE_STABILITY[kind],
                    "metadata": metadata,
                    "created_at": str(row["created_at"]),
                }
            )
        return sources

    def provenance_summary(
        self,
        *,
        user_id: str,
        node_id: int | None = None,
        edge_id: int | None = None,
    ) -> dict[str, Any]:
        sources = self._linked_sources(user_id=user_id, node_id=node_id, edge_id=edge_id)
        support_count = 1
        conflict_count = 0
        if edge_id is not None:
            with self._lock:
                edge = self._conn.execute(
                    "SELECT support_count FROM graph_edges WHERE user_id=? AND edge_id=?",
                    (user_id, int(edge_id)),
                ).fetchone()
                if edge is None:
                    raise LookupError(f"edge_id {edge_id} is outside user graph scope")
                signal = self._conn.execute(
                    "SELECT conflict_count FROM graph_edge_signals WHERE user_id=? AND edge_id=?",
                    (user_id, int(edge_id)),
                ).fetchone()
            support_count = int(edge["support_count"])
            conflict_count = 0 if signal is None else int(signal["conflict_count"])

        confidence, stability, dominant_kind = self._confidence(
            sources=sources,
            support_count=support_count,
            conflict_count=conflict_count,
        )
        return {
            "target": {"node_id": int(node_id)} if node_id is not None else {"edge_id": int(edge_id)},
            "confidence": confidence,
            "source_kind": dominant_kind,
            "support_count": support_count,
            "conflict_count": conflict_count,
            "stability": stability,
            "sources": sources,
        }

    def compact_edge_metadata(self, *, user_id: str, edge_id: int, support_count: int) -> dict[str, Any]:
        sources = self._linked_sources(user_id=user_id, edge_id=edge_id)
        with self._lock:
            signal = self._conn.execute(
                "SELECT conflict_count FROM graph_edge_signals WHERE user_id=? AND edge_id=?",
                (user_id, int(edge_id)),
            ).fetchone()
        conflict_count = 0 if signal is None else int(signal["conflict_count"])
        confidence, _, dominant_kind = self._confidence(
            sources=sources,
            support_count=int(support_count),
            conflict_count=conflict_count,
        )
        return {
            "confidence": confidence,
            "source_kind": dominant_kind,
        }

    @staticmethod
    def _confidence(
        *,
        sources: list[dict[str, Any]],
        support_count: int,
        conflict_count: int,
    ) -> tuple[float, float, str]:
        if sources:
            dominant = max(
                sources,
                key=lambda source: (
                    float(source["source_reliability"]),
                    float(source["stability"]),
                    -int(source["source_id"]),
                ),
            )
            base_reliability = float(dominant["source_reliability"])
            base_stability = float(dominant["stability"])
            dominant_kind = str(dominant["source_kind"])
        else:
            base_reliability = 0.45
            base_stability = 0.35
            dominant_kind = "unlinked"

        support_boost = min(0.18, max(0, int(support_count) - 1) * 0.03)
        conflict_penalty = min(0.30, max(0, int(conflict_count)) * 0.10)
        stability_boost = min(0.15, max(0, int(support_count) - 1) * 0.025)
        stability_penalty = min(0.25, max(0, int(conflict_count)) * 0.08)
        confidence = max(0.0, min(1.0, base_reliability + support_boost - conflict_penalty))
        stability = max(0.0, min(1.0, base_stability + stability_boost - stability_penalty))
        return round(confidence, 3), round(stability, 3), dominant_kind

    def read_source(
        self,
        *,
        user_id: str,
        source_id: int,
        start: int = 1,
        limit: int = 8000,
    ) -> dict[str, Any]:
        start = int(start)
        limit = int(limit)
        if start < 1:
            raise ValueError("source read start must be >= 1")
        if not 1 <= limit <= 12000:
            raise ValueError("source read limit must be between 1 and 12000")
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM graph_sources WHERE user_id=? AND source_id=?",
                (user_id, int(source_id)),
            ).fetchone()
        if row is None:
            raise LookupError(f"source_id {source_id} is outside user source scope")
        content = str(row["content"])
        start_index = start - 1
        end_index = min(len(content), start_index + limit)
        excerpt = content[start_index:end_index]
        metadata = json.loads(str(row["metadata_json"]))
        if not isinstance(metadata, dict):
            raise ValueError("graph source metadata must decode to an object")
        return {
            "source_id": int(row["source_id"]),
            "turn_id": str(row["turn_id"]),
            "source_kind": str(row["source_kind"]),
            "source_key": str(row["source_key"]),
            "metadata": metadata,
            "start": start,
            "content": excerpt,
            "total_chars": len(content),
            "has_more": end_index < len(content),
            "next_start": end_index + 1 if end_index < len(content) else None,
            "created_at": str(row["created_at"]),
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()
