from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from threading import Lock, local
from typing import Any

from .repository import GraphConflictError, GraphRepository as BaseGraphRepository


_GRAPH_SCHEMA_VERSION = "3"
_DIRECT_TURN_ID = "__direct_repository_call__"


class GraphRepository(BaseGraphRepository):
    """Graph repository with one SQLite connection per calling thread."""

    def __init__(self, db_path: str | Path, *, busy_timeout_ms: int = 5000) -> None:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = path
        self._busy_timeout_ms = int(busy_timeout_ms)
        self._local = local()
        self._connections: list[sqlite3.Connection] = []
        self._connections_lock = Lock()
        self._initialize_schema()

    @property
    def _conn(self) -> sqlite3.Connection:
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = self._open_connection()
            self._local.connection = connection
            with self._connections_lock:
                self._connections.append(connection)
        return connection

    def _open_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(self._db_path),
            timeout=self._busy_timeout_ms / 1000,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def _initialize_schema(self) -> None:
        conn = self._conn
        existing_graph = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='graph_nodes'"
        ).fetchone()
        meta_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='graph_schema_meta'"
        ).fetchone()
        if existing_graph is not None:
            if meta_table is None:
                raise RuntimeError("graph database uses a retired schema; delete MAI_GRAPH_DB and restart")
            row = conn.execute("SELECT value FROM graph_schema_meta WHERE key='schema_version'").fetchone()
            if row is None or str(row["value"]) != _GRAPH_SCHEMA_VERSION:
                found = None if row is None else str(row["value"])
                raise RuntimeError(
                    "graph database schema version is incompatible "
                    f"(expected {_GRAPH_SCHEMA_VERSION}, found {found!r}); "
                    "delete MAI_GRAPH_DB and restart"
                )
            self._create_schema()
            return
        if meta_table is not None:
            raise RuntimeError(
                "graph database schema marker exists without graph tables; delete MAI_GRAPH_DB and restart"
            )
        self._create_schema()
        conn.execute("CREATE TABLE graph_schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute(
            "INSERT INTO graph_schema_meta (key, value) VALUES ('schema_version', ?)",
            (_GRAPH_SCHEMA_VERSION,),
        )
        conn.commit()

    def create_edge(
        self,
        *,
        user_id: str,
        start_node_id: int,
        end_node_id: int,
        relation: str,
        weight: float,
        personal_relevance: float,
        turn_id: str = _DIRECT_TURN_ID,
    ) -> dict:
        return super().create_edge(
            user_id=user_id,
            start_node_id=start_node_id,
            end_node_id=end_node_id,
            relation=relation,
            weight=weight,
            personal_relevance=personal_relevance,
            turn_id=turn_id,
        )

    def update_edge(
        self,
        *,
        user_id: str,
        edge_id: int,
        relation: str,
        weight: float,
        personal_relevance: float,
        turn_id: str = _DIRECT_TURN_ID,
    ) -> dict:
        relation = self._required(relation, "relation")
        turn_id = self._required(turn_id, "turn_id")
        weight = float(weight)
        relevance = float(personal_relevance)
        if not 0.0 <= weight <= 1.0:
            raise ValueError("edge weight must be between 0 and 1")
        if relevance not in {0.5, 1.0}:
            raise ValueError("personal_relevance must be 0.5 or 1.0")
        with self.transaction() as conn:
            self._require_owned_edge(conn, user_id=user_id, edge_id=edge_id)
            version_id = self._append_edge_version(
                conn,
                edge_id=int(edge_id),
                relation=relation,
                weight=weight,
                personal_relevance=relevance,
                turn_id=turn_id,
            )
            conn.execute(
                "UPDATE graph_edges SET current_version_id=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND edge_id=?",
                (version_id, user_id, int(edge_id)),
            )
            self._prune_versions_by_turn(conn, edge_id=int(edge_id))
        return self.get_edge(user_id=user_id, edge_id=edge_id)

    def commit_working_graph(
        self,
        *,
        user_id: str,
        turn_id: str,
        embedding_model: str,
        node_embeddings: dict[int, list[float]],
        pending_nodes: dict[int, dict[str, Any]],
        node_updates: dict[int, dict[str, Any]],
        node_merges: list[dict[str, Any]],
        pending_edges: dict[int, dict[str, Any]],
        edge_updates: dict[int, dict[str, Any]],
    ) -> dict[str, Any]:
        """Apply one completed Working Graph as a single semantic transaction."""
        user_id = self._required(user_id, "user_id")
        turn_id = self._required(turn_id, "turn_id")
        embedding_model = self._required(embedding_model, "embedding_model")
        node_id_map: dict[int, int] = {}
        edge_id_map: dict[int, int] = {}

        with self.transaction() as conn:
            self._require_source_tables(conn)

            # Create every pending node first so composites/edges may resolve temp ids.
            for temp_id, payload in pending_nodes.items():
                temp_id = int(temp_id)
                if temp_id >= 0:
                    raise ValueError("pending node ids must be negative")
                name = self._required(str(payload["name"]), "pending node name")
                kind = self._validate_kind(str(payload["kind"]))
                cursor = conn.execute(
                    "INSERT INTO graph_nodes (user_id, name, kind) VALUES (?, ?, ?)",
                    (user_id, name, kind),
                )
                actual_id = int(cursor.lastrowid)
                node_id_map[temp_id] = actual_id
                vector = [float(value) for value in node_embeddings[temp_id]]
                if not vector:
                    raise ValueError("pending node embedding must be non-empty")
                conn.execute(
                    """
                    INSERT INTO graph_node_embeddings (user_id, node_id, model, dimension, vector_json)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (user_id, actual_id, embedding_model, len(vector), json.dumps(vector, separators=(",", ":"))),
                )
                self._link_sources_conn(
                    conn,
                    user_id=user_id,
                    turn_id=turn_id,
                    source_ids=payload.get("source_ids", []),
                    node_id=actual_id,
                )

            # Existing node edits are still pending until this transaction.
            for node_id, update in node_updates.items():
                actual_id = self._resolve_commit_node_id(int(node_id), node_id_map)
                self._require_owned_active_node(conn, user_id=user_id, node_id=actual_id)
                if "name" in update:
                    if self._is_user_anchor(conn, user_id=user_id, node_id=actual_id):
                        raise GraphConflictError("canonical user anchor is framework-managed and cannot be renamed")
                    name = self._required(str(update["name"]), "node name")
                    conn.execute(
                        "UPDATE graph_nodes SET name=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND node_id=?",
                        (name, user_id, actual_id),
                    )
                    vector = [float(value) for value in node_embeddings[actual_id]]
                    if not vector:
                        raise ValueError("renamed node embedding must be non-empty")
                    conn.execute(
                        """
                        INSERT INTO graph_node_embeddings (user_id, node_id, model, dimension, vector_json)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(user_id, node_id) DO UPDATE SET
                            model=excluded.model,
                            dimension=excluded.dimension,
                            vector_json=excluded.vector_json,
                            updated_at=CURRENT_TIMESTAMP
                        """,
                        (user_id, actual_id, embedding_model, len(vector), json.dumps(vector, separators=(",", ":"))),
                    )
                self._link_sources_conn(
                    conn,
                    user_id=user_id,
                    turn_id=turn_id,
                    source_ids=update.get("source_ids", []),
                    node_id=actual_id,
                )

            # Composite structure is applied after all temp ids exist.
            for temp_id, payload in pending_nodes.items():
                if str(payload["kind"]) != "composite":
                    continue
                actual_id = node_id_map[int(temp_id)]
                members = [self._resolve_commit_node_id(int(value), node_id_map) for value in payload.get("member_node_ids", [])]
                self._set_members_conn(conn, user_id=user_id, composite_node_id=actual_id, member_node_ids=members)
            for node_id, update in node_updates.items():
                if "member_node_ids" not in update:
                    continue
                actual_id = self._resolve_commit_node_id(int(node_id), node_id_map)
                members = [self._resolve_commit_node_id(int(value), node_id_map) for value in update["member_node_ids"]]
                self._set_members_conn(conn, user_id=user_id, composite_node_id=actual_id, member_node_ids=members)

            # Existing-node merges are semantic structural operations and must share this transaction.
            merge_map: dict[int, int] = {}
            for merge in node_merges:
                source_id = self._resolve_commit_node_id(int(merge["source_node_id"]), node_id_map)
                target_id = self._resolve_commit_node_id(int(merge["target_node_id"]), node_id_map)
                target_id = self._resolve_merge_target(target_id, merge_map)
                self._merge_node_conn(conn, user_id=user_id, source_id=source_id, target_id=target_id)
                merge_map[source_id] = target_id
                self._link_sources_conn(
                    conn,
                    user_id=user_id,
                    turn_id=turn_id,
                    source_ids=merge.get("source_ids", []),
                    node_id=target_id,
                )

            # Create pending logical edges and their one committed version for this turn.
            for temp_edge_id, payload in pending_edges.items():
                temp_edge_id = int(temp_edge_id)
                if temp_edge_id >= 0:
                    raise ValueError("pending edge ids must be negative")
                start = self._resolve_commit_node_id(int(payload["start_node_id"]), node_id_map)
                end = self._resolve_commit_node_id(int(payload["end_node_id"]), node_id_map)
                start = self._resolve_merge_target(start, merge_map)
                end = self._resolve_merge_target(end, merge_map)
                if start == end:
                    raise GraphConflictError("working edge commit would create a self-loop")
                self._require_owned_active_node(conn, user_id=user_id, node_id=start)
                self._require_owned_active_node(conn, user_id=user_id, node_id=end)
                if conn.execute(
                    "SELECT 1 FROM graph_edges WHERE user_id=? AND start_node_id=? AND end_node_id=?",
                    (user_id, start, end),
                ).fetchone() is not None:
                    raise GraphConflictError("working edge commit collides with an existing directed edge")
                cursor = conn.execute(
                    "INSERT INTO graph_edges (user_id, start_node_id, end_node_id) VALUES (?, ?, ?)",
                    (user_id, start, end),
                )
                edge_id = int(cursor.lastrowid)
                edge_id_map[temp_edge_id] = edge_id
                version_id = self._append_edge_version(
                    conn,
                    edge_id=edge_id,
                    relation=self._required(str(payload["relation"]), "edge relation"),
                    weight=float(payload["weight"]),
                    personal_relevance=float(payload["personal_relevance"]),
                    turn_id=turn_id,
                )
                conn.execute(
                    "UPDATE graph_edges SET current_version_id=? WHERE edge_id=?",
                    (version_id, edge_id),
                )
                self._link_sources_conn(
                    conn,
                    user_id=user_id,
                    turn_id=turn_id,
                    source_ids=payload.get("source_ids", []),
                    edge_version_id=version_id,
                )

            # Existing edge fixes append exactly one final committed version per edge for this turn.
            for edge_id, payload in edge_updates.items():
                edge_id = int(edge_id)
                edge = self._require_owned_edge(conn, user_id=user_id, edge_id=edge_id)
                expected_start = self._resolve_merge_target(int(edge["start_node_id"]), merge_map)
                expected_end = self._resolve_merge_target(int(edge["end_node_id"]), merge_map)
                working_start = self._resolve_commit_node_id(int(payload["start_node_id"]), node_id_map)
                working_end = self._resolve_commit_node_id(int(payload["end_node_id"]), node_id_map)
                working_start = self._resolve_merge_target(working_start, merge_map)
                working_end = self._resolve_merge_target(working_end, merge_map)
                if (expected_start, expected_end) != (working_start, working_end):
                    raise GraphConflictError("working edge endpoints diverged from committed merge result")
                version_id = self._append_edge_version(
                    conn,
                    edge_id=edge_id,
                    relation=self._required(str(payload["relation"]), "edge relation"),
                    weight=float(payload["weight"]),
                    personal_relevance=float(payload["personal_relevance"]),
                    turn_id=turn_id,
                )
                conn.execute(
                    "UPDATE graph_edges SET current_version_id=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND edge_id=?",
                    (version_id, user_id, edge_id),
                )
                self._link_sources_conn(
                    conn,
                    user_id=user_id,
                    turn_id=turn_id,
                    source_ids=payload.get("source_ids", []),
                    edge_version_id=version_id,
                )
                self._prune_versions_by_turn(conn, edge_id=edge_id)

        return {
            "status": "committed",
            "turn_id": turn_id,
            "node_id_map": node_id_map,
            "edge_id_map": edge_id_map,
        }

    @staticmethod
    def _resolve_commit_node_id(node_id: int, node_id_map: dict[int, int]) -> int:
        value = int(node_id)
        if value < 0:
            try:
                return int(node_id_map[value])
            except KeyError as exc:
                raise GraphConflictError(f"unknown pending node id during commit: {value}") from exc
        return value

    @staticmethod
    def _resolve_merge_target(node_id: int, merge_map: dict[int, int]) -> int:
        current = int(node_id)
        seen: set[int] = set()
        while current in merge_map:
            if current in seen:
                raise GraphConflictError("node merge map contains a cycle")
            seen.add(current)
            current = int(merge_map[current])
        return current

    @staticmethod
    def _require_source_tables(conn: sqlite3.Connection) -> None:
        for table in ("graph_sources", "graph_source_links"):
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone() is None:
                raise RuntimeError(f"graph source table is missing during commit: {table}")

    @staticmethod
    def _link_sources_conn(
        conn: sqlite3.Connection,
        *,
        user_id: str,
        turn_id: str,
        source_ids: Any,
        node_id: int | None = None,
        edge_version_id: int | None = None,
    ) -> None:
        ids = list(dict.fromkeys(int(value) for value in source_ids))
        if not ids:
            return
        if (node_id is None) == (edge_version_id is None):
            raise ValueError("exactly one graph source link target is required")
        for source_id in ids:
            row = conn.execute(
                "SELECT user_id FROM graph_sources WHERE source_id=?",
                (source_id,),
            ).fetchone()
            if row is None or str(row["user_id"]) != user_id:
                raise PermissionError(f"source_id {source_id} is outside user source scope")
            conn.execute(
                """
                INSERT OR IGNORE INTO graph_source_links
                    (user_id, turn_id, source_id, node_id, edge_version_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, turn_id, source_id, node_id, edge_version_id),
            )

    def _set_members_conn(
        self,
        conn: sqlite3.Connection,
        *,
        user_id: str,
        composite_node_id: int,
        member_node_ids: list[int],
    ) -> None:
        members = list(dict.fromkeys(int(value) for value in member_node_ids))
        if len(members) < 2:
            raise GraphConflictError("composite node requires at least two members")
        if int(composite_node_id) in members:
            raise GraphConflictError("composite node cannot contain itself")
        composite = self._require_owned_active_node(conn, user_id=user_id, node_id=composite_node_id)
        if str(composite["kind"]) != "composite":
            raise GraphConflictError("only composite nodes may have structural members")
        conn.execute(
            "DELETE FROM graph_composite_members WHERE user_id=? AND composite_node_id=?",
            (user_id, int(composite_node_id)),
        )
        for member_id in members:
            self._require_owned_active_node(conn, user_id=user_id, node_id=member_id)
            if self._composite_reaches(
                conn,
                user_id=user_id,
                start_node_id=member_id,
                target_node_id=composite_node_id,
            ):
                raise GraphConflictError("composite membership cycle is not allowed")
        conn.executemany(
            "INSERT INTO graph_composite_members (user_id, composite_node_id, member_node_id) VALUES (?, ?, ?)",
            [(user_id, int(composite_node_id), member_id) for member_id in members],
        )
        conn.execute(
            "UPDATE graph_nodes SET updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND node_id=?",
            (user_id, int(composite_node_id)),
        )

    def _merge_node_conn(
        self,
        conn: sqlite3.Connection,
        *,
        user_id: str,
        source_id: int,
        target_id: int,
    ) -> None:
        if source_id == target_id:
            raise GraphConflictError("merge source and target must differ")
        source = self._require_owned_active_node(conn, user_id=user_id, node_id=source_id)
        target = self._require_owned_active_node(conn, user_id=user_id, node_id=target_id)
        if self._is_user_anchor(conn, user_id=user_id, node_id=source_id):
            raise GraphConflictError("canonical user anchor cannot be merged away")

        source_members = [
            int(row["member_node_id"])
            for row in conn.execute(
                "SELECT member_node_id FROM graph_composite_members WHERE user_id=? AND composite_node_id=? ORDER BY member_node_id",
                (user_id, source_id),
            ).fetchall()
        ]
        target_members = [
            int(row["member_node_id"])
            for row in conn.execute(
                "SELECT member_node_id FROM graph_composite_members WHERE user_id=? AND composite_node_id=? ORDER BY member_node_id",
                (user_id, target_id),
            ).fetchall()
        ]
        if source_members:
            if str(source["kind"]) != "composite" or str(target["kind"]) != "composite":
                raise GraphConflictError("merging a composite node into a non-composite node would lose structural members")
            merged_members = list(dict.fromkeys([*target_members, *source_members]))
            if target_id in merged_members:
                raise GraphConflictError("node merge would create composite self-membership")
        else:
            merged_members = target_members

        parent_ids = [
            int(row["composite_node_id"])
            for row in conn.execute(
                "SELECT composite_node_id FROM graph_composite_members WHERE user_id=? AND member_node_id=? ORDER BY composite_node_id",
                (user_id, source_id),
            ).fetchall()
        ]
        for composite_id in parent_ids:
            if composite_id == target_id:
                raise GraphConflictError("node merge would create composite self-membership")
            current_members = [
                int(row["member_node_id"])
                for row in conn.execute(
                    "SELECT member_node_id FROM graph_composite_members WHERE user_id=? AND composite_node_id=? ORDER BY member_node_id",
                    (user_id, composite_id),
                ).fetchall()
            ]
            replaced = {target_id if member == source_id else member for member in current_members}
            if len(replaced) < 2:
                raise GraphConflictError("node merge would leave a parent composite with fewer than two members")

        incident = conn.execute(
            "SELECT edge_id, start_node_id, end_node_id FROM graph_edges WHERE user_id=? AND (start_node_id=? OR end_node_id=?) ORDER BY edge_id",
            (user_id, source_id, source_id),
        ).fetchall()
        for row in incident:
            edge_id = int(row["edge_id"])
            new_start = target_id if int(row["start_node_id"]) == source_id else int(row["start_node_id"])
            new_end = target_id if int(row["end_node_id"]) == source_id else int(row["end_node_id"])
            if new_start == new_end:
                raise GraphConflictError("node merge would create a self-loop; fix/disconnect that edge first")
            conflict = conn.execute(
                "SELECT edge_id FROM graph_edges WHERE user_id=? AND start_node_id=? AND end_node_id=? AND edge_id<>?",
                (user_id, new_start, new_end, edge_id),
            ).fetchone()
            if conflict is not None:
                raise GraphConflictError("node merge would collide with another directed edge; fix/disconnect the conflict first")

        for row in incident:
            edge_id = int(row["edge_id"])
            new_start = target_id if int(row["start_node_id"]) == source_id else int(row["start_node_id"])
            new_end = target_id if int(row["end_node_id"]) == source_id else int(row["end_node_id"])
            conn.execute(
                "UPDATE graph_edges SET start_node_id=?, end_node_id=?, updated_at=CURRENT_TIMESTAMP WHERE edge_id=?",
                (new_start, new_end, edge_id),
            )

        for composite_id in parent_ids:
            conn.execute(
                "INSERT OR IGNORE INTO graph_composite_members (user_id, composite_node_id, member_node_id) VALUES (?, ?, ?)",
                (user_id, composite_id, target_id),
            )
        conn.execute(
            "DELETE FROM graph_composite_members WHERE user_id=? AND member_node_id=?",
            (user_id, source_id),
        )
        if source_members:
            conn.execute(
                "DELETE FROM graph_composite_members WHERE user_id=? AND composite_node_id=?",
                (user_id, target_id),
            )
            conn.executemany(
                "INSERT INTO graph_composite_members (user_id, composite_node_id, member_node_id) VALUES (?, ?, ?)",
                [(user_id, target_id, member_id) for member_id in merged_members],
            )
        conn.execute(
            "DELETE FROM graph_composite_members WHERE user_id=? AND composite_node_id=?",
            (user_id, source_id),
        )
        conn.execute(
            "UPDATE graph_nodes SET is_active=0, updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND node_id=?",
            (user_id, source_id),
        )
        conn.execute(
            "UPDATE graph_nodes SET updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND node_id=?",
            (user_id, target_id),
        )

    @staticmethod
    def _prune_versions_by_turn(conn: sqlite3.Connection, *, edge_id: int) -> None:
        rows = conn.execute(
            "SELECT version_id, turn_id FROM graph_edge_versions WHERE edge_id=? ORDER BY version_id DESC",
            (int(edge_id),),
        ).fetchall()
        kept_turns: set[str] = set()
        stale: list[int] = []
        for row in rows:
            version_turn = str(row["turn_id"])
            if version_turn in kept_turns or len(kept_turns) >= 3:
                stale.append(int(row["version_id"]))
                continue
            kept_turns.add(version_turn)
        if stale:
            placeholders = ",".join("?" for _ in stale)
            conn.execute(
                f"DELETE FROM graph_edge_versions WHERE version_id IN ({placeholders})",
                tuple(stale),
            )

    def close(self) -> None:
        with self._connections_lock:
            connections = list(self._connections)
            self._connections.clear()
        for connection in connections:
            connection.close()
        self._local.__dict__.clear()
