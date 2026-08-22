from pathlib import Path

import pytest

from mai.graph import GraphRepository, GraphSourceStore, SourceRecord


def _edge(repository: GraphRepository, *, user_id: str, turn_id: str) -> dict:
    repository.ensure_user_anchor(user_id=user_id, turn_id=turn_id, source_text="init")
    with repository.transaction() as conn:
        anchor = conn.execute(
            "SELECT node_id FROM graph_user_anchors WHERE user_id=?",
            (user_id,),
        ).fetchone()
        assert anchor is not None
        cursor = conn.execute(
            "INSERT INTO graph_nodes (user_id, name) VALUES (?, ?)",
            (user_id, "memory"),
        )
        object_id = int(cursor.lastrowid)
        edge_cursor = conn.execute(
            "INSERT INTO graph_edges (user_id, subject_node_id, relation, object_node_id) VALUES (?, ?, ?, ?)",
            (user_id, int(anchor["node_id"]), "knows", object_id),
        )
        edge_id = int(edge_cursor.lastrowid)
    return repository.get_edge(user_id=user_id, edge_id=edge_id)


def test_source_summary_is_compact_and_raw_content_is_lazy(tmp_path: Path) -> None:
    db = tmp_path / "graph.sqlite3"
    repository = GraphRepository(db)
    sources = GraphSourceStore(db)
    try:
        edge = _edge(repository, user_id="u", turn_id="t")
        with repository.transaction() as conn:
            source_ids = sources.ensure_sources_in_connection(
                conn,
                user_id="u",
                turn_id="t",
                records=[
                    SourceRecord(
                        source_kind="web_evidence",
                        source_key="tool:1",
                        content="raw evidence text",
                        metadata={"url": "https://example.com"},
                    )
                ],
            )
            sources.link_sources_in_connection(
                conn,
                user_id="u",
                turn_id="t",
                source_ids=source_ids,
                edge_id=int(edge["edge_id"]),
            )

        summary = sources.provenance_summary(user_id="u", edge_id=int(edge["edge_id"]))
        assert summary["source_kind"] == "web_evidence"
        assert summary["confidence"] == 0.82
        assert summary["support_count"] == 1
        assert summary["conflict_count"] == 0
        assert "raw evidence text" not in str(summary)
        assert summary["sources"][0]["source_id"] == source_ids[0]

        raw = sources.read_source(user_id="u", source_id=source_ids[0], start=1, limit=3)
        assert raw["content"] == "raw"
        assert raw["has_more"] is True
        assert raw["next_start"] == 4
    finally:
        sources.close()
        repository.close()


def test_support_strengthens_and_conflict_weakens_structural_confidence(tmp_path: Path) -> None:
    db = tmp_path / "graph.sqlite3"
    repository = GraphRepository(db)
    sources = GraphSourceStore(db)
    try:
        edge = _edge(repository, user_id="u", turn_id="t")
        edge_id = int(edge["edge_id"])
        with repository.transaction() as conn:
            source_ids = sources.ensure_sources_in_connection(
                conn,
                user_id="u",
                turn_id="t",
                records=[
                    SourceRecord(
                        source_kind="assistant_message",
                        source_key="assistant",
                        content="model statement",
                        metadata={"factual_status": "unverified"},
                    )
                ],
            )
            sources.link_sources_in_connection(
                conn,
                user_id="u",
                turn_id="t",
                source_ids=source_ids,
                edge_id=edge_id,
            )

        base = sources.provenance_summary(user_id="u", edge_id=edge_id)["confidence"]
        with repository.transaction() as conn:
            conn.execute(
                "UPDATE graph_edges SET support_count=support_count+2 WHERE user_id=? AND edge_id=?",
                ("u", edge_id),
            )
        strengthened = sources.provenance_summary(user_id="u", edge_id=edge_id)["confidence"]
        assert strengthened > base

        with repository.transaction() as conn:
            sources.record_edge_conflict_in_connection(conn, user_id="u", edge_id=edge_id)
        weakened = sources.provenance_summary(user_id="u", edge_id=edge_id)["confidence"]
        assert weakened < strengthened
    finally:
        sources.close()
        repository.close()


def test_stable_source_identity_collision_is_visible(tmp_path: Path) -> None:
    db = tmp_path / "graph.sqlite3"
    repository = GraphRepository(db)
    sources = GraphSourceStore(db)
    try:
        with repository.transaction() as conn:
            sources.ensure_sources_in_connection(
                conn,
                user_id="u",
                turn_id="t",
                records=[SourceRecord("user_message", "user", "first", {})],
            )
        with pytest.raises(RuntimeError, match="identity collision"):
            with repository.transaction() as conn:
                sources.ensure_sources_in_connection(
                    conn,
                    user_id="u",
                    turn_id="t",
                    records=[SourceRecord("user_message", "user", "different", {})],
                )
    finally:
        sources.close()
        repository.close()
