from __future__ import annotations

from mai.graph import GraphRepository, GraphSourceStore, SourceRecord


def test_sources_link_independently_to_nodes_and_edges(tmp_path) -> None:
    path = tmp_path / "graph.db"
    repo = GraphRepository(path)
    sources = GraphSourceStore(path)
    try:
        a = repo.create_node(user_id="u", name="A")
        b = repo.create_node(user_id="u", name="B")
        edge = repo.create_edge(
            user_id="u",
            start_node_id=a["node_id"],
            end_node_id=b["node_id"],
            relation="related",
            weight=0.8,
            personal_relevance=0.5,
        )
        source_id = sources.ensure_sources(
            user_id="u",
            turn_id="t1",
            records=[
                SourceRecord(
                    source_kind="user_message",
                    source_key="user",
                    content="A is related to B",
                    metadata={},
                )
            ],
        )[0]

        sources.link_sources(
            user_id="u",
            turn_id="t1",
            source_ids=[source_id],
            node_id=a["node_id"],
        )
        sources.link_sources(
            user_id="u",
            turn_id="t1",
            source_ids=[source_id],
            edge_id=edge["edge_id"],
        )

        assert sources.source_ids_for_node(user_id="u", node_id=a["node_id"]) == [source_id]
        assert sources.source_ids_for_edge(user_id="u", edge_id=edge["edge_id"]) == [source_id]
        assert sources.read_source(user_id="u", source_id=source_id)["content"] == "A is related to B"
    finally:
        sources.close()
        repo.close()


def test_stable_source_identity_rejects_different_content(tmp_path) -> None:
    path = tmp_path / "graph.db"
    repo = GraphRepository(path)
    sources = GraphSourceStore(path)
    try:
        sources.ensure_sources(
            user_id="u",
            turn_id="t1",
            records=[SourceRecord("user_message", "user", "first", {})],
        )

        try:
            sources.ensure_sources(
                user_id="u",
                turn_id="t1",
                records=[SourceRecord("user_message", "user", "different", {})],
            )
        except RuntimeError as exc:
            assert "identity collision" in str(exc)
        else:
            raise AssertionError("source identity collision must fail visibly")
    finally:
        sources.close()
        repo.close()
