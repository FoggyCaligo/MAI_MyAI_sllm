from __future__ import annotations

import sqlite3

import pytest

from mai.graph.repository import GraphRepository, GraphScopeError


def _node(repo: GraphRepository, user: str, name: str, turn: str = "t1") -> dict:
    return repo.create_node(
        user_id=user,
        name=name,
        turn_id=turn,
        source_role="user",
        source_text=f"source:{name}",
    )


def test_node_creation_is_user_scoped_and_records_provenance(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "graph.db")
    try:
        node = _node(repo, "owner", "로봇공학")
        assert node["name"] == "로봇공학"
        assert node["user_id"] == "owner"
        provenance = repo.provenance_for_turn(user_id="owner", turn_id="t1")
        assert len(provenance) == 1
        assert provenance[0]["node_id"] == node["node_id"]
        assert provenance[0]["source_role"] == "user"
    finally:
        repo.close()


def test_same_node_name_is_not_implicitly_semantically_merged(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "graph.db")
    try:
        first = _node(repo, "owner", "Python")
        second = _node(repo, "owner", "Python", turn="t2")
        assert first["node_id"] != second["node_id"]
    finally:
        repo.close()


def test_edge_reinforcement_is_scoped_and_increments_support(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "graph.db")
    try:
        user = _node(repo, "owner", "사용자")
        tea = _node(repo, "owner", "차")
        first = repo.create_or_reinforce_edge(
            user_id="owner",
            subject_node_id=user["node_id"],
            relation="좋아한다",
            object_node_id=tea["node_id"],
            turn_id="t3",
            source_role="turn",
            source_text="사용자는 차를 좋아한다",
        )
        second = repo.create_or_reinforce_edge(
            user_id="owner",
            subject_node_id=user["node_id"],
            relation="좋아한다",
            object_node_id=tea["node_id"],
            turn_id="t4",
            source_role="turn",
            source_text="사용자는 여전히 차를 좋아한다",
        )
        assert first["edge_id"] == second["edge_id"]
        assert second["support_count"] == 2
        assert len(repo.provenance_for_turn(user_id="owner", turn_id="t4")) == 1
    finally:
        repo.close()


def test_cross_user_edge_is_rejected(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "graph.db")
    try:
        left = _node(repo, "owner", "A")
        right = _node(repo, "other", "B")
        with pytest.raises(GraphScopeError):
            repo.create_or_reinforce_edge(
                user_id="owner",
                subject_node_id=left["node_id"],
                relation="knows",
                object_node_id=right["node_id"],
                turn_id="t2",
                source_role="turn",
                source_text="A knows B",
            )
    finally:
        repo.close()


def test_edge_revision_preserves_id_and_rejects_collision(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "graph.db")
    try:
        user = _node(repo, "owner", "사용자")
        coffee = _node(repo, "owner", "커피")
        tea = _node(repo, "owner", "차")
        edge = repo.create_or_reinforce_edge(
            user_id="owner",
            subject_node_id=user["node_id"],
            relation="좋아한다",
            object_node_id=coffee["node_id"],
            turn_id="t1",
            source_role="turn",
            source_text="커피를 좋아한다",
        )
        revised = repo.revise_edge(
            user_id="owner",
            edge_id=edge["edge_id"],
            subject_node_id=user["node_id"],
            relation="좋아한다",
            object_node_id=tea["node_id"],
            turn_id="t2",
            source_role="turn",
            source_text="커피가 아니라 차를 좋아한다",
        )
        assert revised["edge_id"] == edge["edge_id"]
        assert revised["object_node_id"] == tea["node_id"]

        repo.create_or_reinforce_edge(
            user_id="owner",
            subject_node_id=user["node_id"],
            relation="싫어한다",
            object_node_id=coffee["node_id"],
            turn_id="t3",
            source_role="turn",
            source_text="커피를 싫어한다",
        )
        with pytest.raises(sqlite3.IntegrityError):
            repo.revise_edge(
                user_id="owner",
                edge_id=edge["edge_id"],
                subject_node_id=user["node_id"],
                relation="싫어한다",
                object_node_id=coffee["node_id"],
                turn_id="t4",
                source_role="turn",
                source_text="collision",
            )
    finally:
        repo.close()


def test_foreign_node_and_edge_reads_fail_explicitly(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "graph.db")
    try:
        a = _node(repo, "a", "A")
        b = _node(repo, "a", "B")
        edge = repo.create_or_reinforce_edge(
            user_id="a",
            subject_node_id=a["node_id"],
            relation="rel",
            object_node_id=b["node_id"],
            turn_id="t1",
            source_role="turn",
            source_text="A rel B",
        )
        with pytest.raises(GraphScopeError):
            repo.get_node(user_id="b", node_id=a["node_id"])
        with pytest.raises(GraphScopeError):
            repo.get_edge(user_id="b", edge_id=edge["edge_id"])
    finally:
        repo.close()
