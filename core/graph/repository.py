from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ... import config
from .models import GraphEdge, GraphNode


class GraphRepository:
    """SQLite-backed graph repository for MK5."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path == ":memory:":
            self._conn = sqlite3.connect(":memory:")
        else:
            resolved_path = Path(db_path or config.DB_PATH).resolve()
            resolved_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(resolved_path))
        self._conn.row_factory = sqlite3.Row
        self._initialize_schema()

    def get_node(self, node_id: str) -> GraphNode | None:
        row = self._conn.execute(
            """
            SELECT *
            FROM graph_nodes
            WHERE node_id = ?
            """,
            (node_id,),
        ).fetchone()
        return self._node_from_row(row) if row is not None else None

    def upsert_node(self, node: GraphNode) -> None:
        self._conn.execute(
            """
            INSERT INTO graph_nodes
                (node_id, labels_json, node_type, payload_json, provenance,
                 trust_score, stability_score, is_active, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(node_id) DO UPDATE SET
                labels_json = excluded.labels_json,
                node_type = excluded.node_type,
                payload_json = excluded.payload_json,
                provenance = excluded.provenance,
                trust_score = excluded.trust_score,
                stability_score = excluded.stability_score,
                is_active = excluded.is_active,
                updated_at = excluded.updated_at
            """,
            (
                node.node_id,
                json.dumps(node.labels, ensure_ascii=False),
                node.node_type,
                json.dumps(node.payload, ensure_ascii=False, sort_keys=True),
                node.provenance,
                node.trust_score,
                node.stability_score,
                int(node.is_active),
                self._now(),
            ),
        )
        self._conn.commit()

    def add_edge(self, edge: GraphEdge) -> None:
        existing = self._conn.execute(
            """
            SELECT edge_id, support_count, conflict_count, trust_score, edge_weight
            FROM graph_edges
            WHERE source_id = ? AND target_id = ? AND relation = ?
            ORDER BY edge_id ASC
            LIMIT 1
            """,
            (edge.source_id, edge.target_id, edge.relation),
        ).fetchone()
        if existing is not None:
            self._conn.execute(
                """
                UPDATE graph_edges SET
                    payload_json = ?, provenance = ?, support_count = ?,
                    conflict_count = ?, trust_score = ?, edge_weight = ?,
                    is_active = ?, updated_at = ?
                WHERE edge_id = ?
                """,
                (
                    json.dumps(edge.payload, ensure_ascii=False, sort_keys=True),
                    edge.provenance,
                    int(existing["support_count"]) + edge.support_count,
                    int(existing["conflict_count"]) + edge.conflict_count,
                    max(float(existing["trust_score"]), edge.trust_score),
                    min(2.0, float(existing["edge_weight"]) + 0.05),
                    int(edge.is_active),
                    self._now(),
                    int(existing["edge_id"]),
                ),
            )
            self._conn.commit()
            return

        self._conn.execute(
            """
            INSERT INTO graph_edges
                (source_id, target_id, relation, payload_json, provenance,
                 support_count, conflict_count, trust_score, edge_weight,
                 is_active, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                edge.source_id,
                edge.target_id,
                edge.relation,
                json.dumps(edge.payload, ensure_ascii=False, sort_keys=True),
                edge.provenance,
                edge.support_count,
                edge.conflict_count,
                edge.trust_score,
                edge.edge_weight,
                int(edge.is_active),
                self._now(),
            ),
        )
        self._conn.commit()

    def neighbors(self, node_id: str) -> list[GraphNode]:
        neighbor_ids: set[str] = set()
        rows = self._conn.execute(
            """
            SELECT source_id, target_id
            FROM graph_edges
            WHERE source_id = ? OR target_id = ?
            ORDER BY edge_id ASC
            """,
            (node_id, node_id),
        ).fetchall()
        for row in rows:
            source_id = str(row["source_id"])
            target_id = str(row["target_id"])
            if source_id == node_id:
                neighbor_ids.add(target_id)
            elif target_id == node_id:
                neighbor_ids.add(source_id)
        return [node for nid in sorted(neighbor_ids) if (node := self.get_node(nid)) is not None]

    def all_nodes(self) -> list[GraphNode]:
        rows = self._conn.execute(
            """
            SELECT *
            FROM graph_nodes
            ORDER BY node_id ASC
            """
        ).fetchall()
        return [self._node_from_row(row) for row in rows]

    def all_edges(self) -> list[GraphEdge]:
        rows = self._conn.execute(
            """
            SELECT *
            FROM graph_edges
            ORDER BY edge_id ASC
            """
        ).fetchall()
        return [self._edge_from_row(row) for row in rows]

    def edges_for_node(self, node_id: str) -> list[GraphEdge]:
        rows = self._conn.execute(
            """
            SELECT *
            FROM graph_edges
            WHERE source_id = ? OR target_id = ?
            ORDER BY edge_id ASC
            """,
            (node_id, node_id),
        ).fetchall()
        return [self._edge_from_row(row) for row in rows]

    def search_nodes(self, query: str, *, limit: int = 8) -> list[GraphNode]:
        terms = {term for term in query.strip().lower().split() if term}
        if not terms:
            return []

        scored: list[tuple[float, GraphNode]] = []
        for node in self.all_nodes():
            if not node.is_active:
                continue
            haystack = " ".join(node.labels).lower()
            matched = {term for term in terms if term in haystack}
            if not matched:
                continue
            score = (len(matched) / len(terms)) * 4.0
            if node.labels and query.strip().lower() == node.labels[0].lower():
                score += 3.0
            score += node.trust_score + node.stability_score
            scored.append((score, node))

        scored.sort(key=lambda item: (-item[0], item[1].node_id))
        return [node for _, node in scored[:limit]]

    def delete_user_graph(self, *, user_id: str, anchor_id: str) -> dict[str, int]:
        """Delete one user's private graph material and now-orphaned concepts."""
        owned_node_ids: set[str] = set()
        if self.get_node(anchor_id) is not None:
            owned_node_ids.add(anchor_id)
        for row in self._conn.execute("SELECT node_id, payload_json FROM graph_nodes").fetchall():
            payload = self._decode_json_dict(str(row["payload_json"]))
            if str(payload.get("user_id") or "") == user_id:
                owned_node_ids.add(str(row["node_id"]))

        edge_ids: set[int] = set()
        for row in self._conn.execute(
            "SELECT edge_id, source_id, target_id, payload_json FROM graph_edges"
        ).fetchall():
            payload = self._decode_json_dict(str(row["payload_json"]))
            if (
                str(row["source_id"]) in owned_node_ids
                or str(row["target_id"]) in owned_node_ids
                or str(payload.get("user_id") or "") == user_id
            ):
                edge_ids.add(int(row["edge_id"]))

        if edge_ids:
            placeholders = ",".join("?" for _ in edge_ids)
            self._conn.execute(
                f"DELETE FROM graph_edges WHERE edge_id IN ({placeholders})",
                tuple(sorted(edge_ids)),
            )
        if owned_node_ids:
            placeholders = ",".join("?" for _ in owned_node_ids)
            self._conn.execute(
                f"DELETE FROM graph_nodes WHERE node_id IN ({placeholders})",
                tuple(sorted(owned_node_ids)),
            )

        orphan_rows = self._conn.execute(
            """
            SELECT n.node_id
            FROM graph_nodes AS n
            LEFT JOIN graph_edges AS e
              ON e.source_id = n.node_id OR e.target_id = n.node_id
            WHERE n.node_type = 'concept' AND n.provenance = 'ingest'
            GROUP BY n.node_id
            HAVING COUNT(e.edge_id) = 0
            """
        ).fetchall()
        orphan_ids = [str(row["node_id"]) for row in orphan_rows]
        if orphan_ids:
            placeholders = ",".join("?" for _ in orphan_ids)
            self._conn.execute(
                f"DELETE FROM graph_nodes WHERE node_id IN ({placeholders})",
                tuple(orphan_ids),
            )
        self._conn.commit()
        return {
            "deleted_nodes": len(owned_node_ids),
            "deleted_edges": len(edge_ids),
            "deleted_orphan_concepts": len(orphan_ids),
        }

    def close(self) -> None:
        self._conn.close()

    def _initialize_schema(self) -> None:
        self._conn.executescript(
            """
            PRAGMA journal_mode=WAL;

            CREATE TABLE IF NOT EXISTS graph_nodes (
                node_id TEXT PRIMARY KEY,
                labels_json TEXT NOT NULL,
                node_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                provenance TEXT NOT NULL DEFAULT 'unknown',
                trust_score REAL NOT NULL DEFAULT 0.5,
                stability_score REAL NOT NULL DEFAULT 0.5,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS graph_edges (
                edge_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                relation TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                provenance TEXT NOT NULL DEFAULT 'unknown',
                support_count INTEGER NOT NULL DEFAULT 1,
                conflict_count INTEGER NOT NULL DEFAULT 0,
                trust_score REAL NOT NULL DEFAULT 0.5,
                edge_weight REAL NOT NULL DEFAULT 1.0,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                UNIQUE(source_id, target_id, relation)
            );

            CREATE INDEX IF NOT EXISTS idx_graph_edges_source_id ON graph_edges(source_id);
            CREATE INDEX IF NOT EXISTS idx_graph_edges_target_id ON graph_edges(target_id);
            """
        )
        self._migrate_legacy_schema()
        self._conn.commit()

    def _node_from_row(self, row: sqlite3.Row) -> GraphNode:
        return GraphNode(
            node_id=str(row["node_id"]),
            labels=self._decode_json_list(str(row["labels_json"])),
            node_type=str(row["node_type"]),
            payload=self._decode_json_dict(str(row["payload_json"])),
            provenance=str(row["provenance"]),
            trust_score=float(row["trust_score"]),
            stability_score=float(row["stability_score"]),
            is_active=bool(row["is_active"]),
        )

    def _edge_from_row(self, row: sqlite3.Row) -> GraphEdge:
        return GraphEdge(
            source_id=str(row["source_id"]),
            target_id=str(row["target_id"]),
            relation=str(row["relation"]),
            payload=self._decode_json_dict(str(row["payload_json"])),
            provenance=str(row["provenance"]),
            support_count=int(row["support_count"]),
            conflict_count=int(row["conflict_count"]),
            trust_score=float(row["trust_score"]),
            edge_weight=float(row["edge_weight"]),
            is_active=bool(row["is_active"]),
        )

    def _migrate_legacy_schema(self) -> None:
        node_columns = {str(row["name"]) for row in self._conn.execute("PRAGMA table_info(graph_nodes)")}
        edge_columns = {str(row["name"]) for row in self._conn.execute("PRAGMA table_info(graph_edges)")}
        node_additions = {
            "provenance": "TEXT NOT NULL DEFAULT 'unknown'",
            "trust_score": "REAL NOT NULL DEFAULT 0.5",
            "stability_score": "REAL NOT NULL DEFAULT 0.5",
            "is_active": "INTEGER NOT NULL DEFAULT 1",
            "created_at": "TEXT NOT NULL DEFAULT ''",
            "updated_at": "TEXT NOT NULL DEFAULT ''",
        }
        edge_additions = {
            "provenance": "TEXT NOT NULL DEFAULT 'unknown'",
            "support_count": "INTEGER NOT NULL DEFAULT 1",
            "conflict_count": "INTEGER NOT NULL DEFAULT 0",
            "trust_score": "REAL NOT NULL DEFAULT 0.5",
            "edge_weight": "REAL NOT NULL DEFAULT 1.0",
            "is_active": "INTEGER NOT NULL DEFAULT 1",
            "created_at": "TEXT NOT NULL DEFAULT ''",
            "updated_at": "TEXT NOT NULL DEFAULT ''",
        }
        for name, ddl in node_additions.items():
            if name not in node_columns:
                self._conn.execute(f"ALTER TABLE graph_nodes ADD COLUMN {name} {ddl}")
        for name, ddl in edge_additions.items():
            if name not in edge_columns:
                self._conn.execute(f"ALTER TABLE graph_edges ADD COLUMN {name} {ddl}")

        # Legacy DBs used payload as part of uniqueness. A second index gives new
        # observations a stable semantic edge identity without rewriting old data.
        try:
            self._conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_graph_edges_semantic "
                "ON graph_edges(source_id, target_id, relation)"
            )
        except sqlite3.IntegrityError:
            pass

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _decode_json_list(self, raw: str) -> list[str]:
        data = json.loads(raw)
        if not isinstance(data, list):
            return []
        return [str(item) for item in data]

    def _decode_json_dict(self, raw: str) -> dict:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return {}
        return dict(data)

