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
    }
)


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
    """Durable evidence units and node/edge source links."""

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

    def linked_sources(
        self,
        *,
        user_id: str,
        node_id: int | None = None,
        edge_id: int | None = None,
    ) -> list[dict[str, Any]]:
        if (node_id is None) == (edge_id is None):
            raise ValueError("exactly one graph source target is required")
        field = "node_id" if node_id is not None else "edge_id"
        target = int(node_id if node_id is not None else edge_id)
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT s.source_id, s.turn_id, s.source_kind, s.source_key,
                       s.metadata_json, s.created_at
                FROM graph_source_links l
                JOIN graph_sources s ON s.source_id=l.source_id AND s.user_id=l.user_id
                WHERE l.user_id=? AND l.{field}=?
                ORDER BY l.link_id
                """,
                (user_id, target),
            ).fetchall()
        return [
            {
                "source_id": int(row["source_id"]),
                "turn_id": str(row["turn_id"]),
                "source_kind": str(row["source_kind"]),
                "source_key": str(row["source_key"]),
                "metadata": json.loads(str(row["metadata_json"])),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def read_source(self, *, user_id: str, source_id: int) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM graph_sources WHERE user_id=? AND source_id=?",
                (user_id, int(source_id)),
            ).fetchone()
        if row is None:
            raise PermissionError(f"source_id {source_id} is outside user source scope")
        return {
            "source_id": int(row["source_id"]),
            "turn_id": str(row["turn_id"]),
            "source_kind": str(row["source_kind"]),
            "source_key": str(row["source_key"]),
            "content": str(row["content"]),
            "metadata": json.loads(str(row["metadata_json"])),
            "created_at": row["created_at"],
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()
