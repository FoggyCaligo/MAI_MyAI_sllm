from __future__ import annotations

import pytest

from mai.graph import GraphConflictError, GraphRepository


def test_directed_pair_allows_one_current_edge_and_reverse_is_distinct(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "graph.db")
    try:
        a = repo.create_node(user_id="u", name="A")
        b = repo.create_node(user_id="u", name="B")
        first = repo.create_edge(
            user_id="u",
            start_node_id=a["node_id"],
            end_node_id=b["node_id"],
            relation="first relation",
            weight=0.8,
            personal_relevance=0.5,
        )

        with pytest.raises(GraphConflictError, match="directed edge already exists"):
            repo.create_edge(
                user_id="u",
                start_node_id=a["node_id"],
                end_node_id=b["node_id"],
                relation="different wording",
                weight=0.7,
                personal_relevance=1.0,
            )

        reverse = repo.create_edge(
            user_id="u",
            start_node_id=b["node_id"],
            end_node_id=a["node_id"],
            relation="reverse relation",
            weight=0.6,
            personal_relevance=0.5,
        )

        assert first["edge_id"] != reverse["edge_id"]
    finally:
        repo.close()


def test_zero_weight_edge_is_excluded_from_active_one_hop(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "graph.db")
    try:
        a = repo.create_node(user_id="u", name="A")
        b = repo.create_node(user_id="u", name="B")
        edge = repo.create_edge(
            user_id="u",
            start_node_id=a["node_id"],
            end_node_id=b["node_id"],
            relation="connected",
            weight=0.5,
            personal_relevance=0.5,
        )
        repo.update_edge(
            user_id="u",
            edge_id=edge["edge_id"],
            relation="connected",
            weight=0.0,
            personal_relevance=0.5,
        )

        neighborhood = repo.one_hop_neighborhood(user_id="u", focus_node_id=a["node_id"])

        assert neighborhood["edges"] == []
        assert [node["node_id"] for node in neighborhood["nodes"]] == [a["node_id"]]
    finally:
        repo.close()


def test_node_embeddings_are_stored_per_configured_model(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "graph.db")
    try:
        node = repo.create_node(user_id="u", name="Mai")
        repo.set_node_embedding(
            user_id="u",
            node_id=node["node_id"],
            model="embed-test",
            vector=[0.1, 0.2, 0.3],
        )

        rows = repo.active_node_embeddings(user_id="u", model="embed-test")

        assert rows == [
            {
                "node_id": node["node_id"],
                "name": "Mai",
                "kind": "concept",
                "dimension": 3,
                "vector": [0.1, 0.2, 0.3],
            }
        ]
    finally:
        repo.close()


def test_composite_membership_rejects_cycles(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "graph.db")
    try:
        a = repo.create_node(user_id="u", name="A", kind="concept")
        b = repo.create_node(user_id="u", name="B", kind="concept")
        c1 = repo.create_node(user_id="u", name="C1", kind="composite")
        c2 = repo.create_node(user_id="u", name="C2", kind="composite")

        repo.set_composite_members(
            user_id="u",
            composite_node_id=c1["node_id"],
            member_node_ids=[a["node_id"], b["node_id"]],
        )
        repo.set_composite_members(
            user_id="u",
            composite_node_id=c2["node_id"],
            member_node_ids=[c1["node_id"], a["node_id"]],
        )

        with pytest.raises(GraphConflictError, match="cycle"):
            repo.set_composite_members(
                user_id="u",
                composite_node_id=c1["node_id"],
                member_node_ids=[c2["node_id"], b["node_id"]],
            )
    finally:
        repo.close()


def test_merge_refuses_semantic_edge_collision_instead_of_choosing(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "graph.db")
    try:
        source = repo.create_node(user_id="u", name="duplicate")
        target = repo.create_node(user_id="u", name="canonical")
        other = repo.create_node(user_id="u", name="other")
        repo.create_edge(
            user_id="u",
            start_node_id=source["node_id"],
            end_node_id=other["node_id"],
            relation="source relation",
            weight=0.5,
            personal_relevance=0.5,
        )
        repo.create_edge(
            user_id="u",
            start_node_id=target["node_id"],
            end_node_id=other["node_id"],
            relation="target relation",
            weight=0.5,
            personal_relevance=0.5,
        )

        with pytest.raises(GraphConflictError, match="collide"):
            repo.merge_node(
                user_id="u",
                source_node_id=source["node_id"],
                target_node_id=target["node_id"],
            )
    finally:
        repo.close()


def test_merge_preserves_members_when_both_nodes_are_composites(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "graph.db")
    try:
        a = repo.create_node(user_id="u", name="A")
        b = repo.create_node(user_id="u", name="B")
        c = repo.create_node(user_id="u", name="C")
        source = repo.create_node(user_id="u", name="duplicate composite", kind="composite")
        target = repo.create_node(user_id="u", name="canonical composite", kind="composite")
        repo.set_composite_members(
            user_id="u",
            composite_node_id=source["node_id"],
            member_node_ids=[a["node_id"], b["node_id"]],
        )
        repo.set_composite_members(
            user_id="u",
            composite_node_id=target["node_id"],
            member_node_ids=[b["node_id"], c["node_id"]],
        )

        repo.merge_node(
            user_id="u",
            source_node_id=source["node_id"],
            target_node_id=target["node_id"],
        )

        assert repo.composite_members(user_id="u", composite_node_id=target["node_id"]) == sorted(
            [a["node_id"], b["node_id"], c["node_id"]]
        )
        assert repo.get_node(user_id="u", node_id=source["node_id"])["is_active"] == 0
    finally:
        repo.close()


def test_merge_rejects_composite_into_concept_instead_of_losing_members(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "graph.db")
    try:
        a = repo.create_node(user_id="u", name="A")
        b = repo.create_node(user_id="u", name="B")
        source = repo.create_node(user_id="u", name="composite duplicate", kind="composite")
        target = repo.create_node(user_id="u", name="concept canonical", kind="concept")
        repo.set_composite_members(
            user_id="u",
            composite_node_id=source["node_id"],
            member_node_ids=[a["node_id"], b["node_id"]],
        )

        with pytest.raises(GraphConflictError, match="would lose structural members"):
            repo.merge_node(
                user_id="u",
                source_node_id=source["node_id"],
                target_node_id=target["node_id"],
            )

        assert repo.composite_members(user_id="u", composite_node_id=source["node_id"]) == sorted(
            [a["node_id"], b["node_id"]]
        )
        assert repo.get_node(user_id="u", node_id=source["node_id"])["is_active"] == 1
    finally:
        repo.close()
