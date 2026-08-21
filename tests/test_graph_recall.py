from __future__ import annotations

import pytest

from mai.graph import GraphRecallService, GraphRepository, GraphScopeError


def _anchor(repo: GraphRepository, user_id: str) -> dict:
    return repo.ensure_user_anchor(
        user_id=user_id,
        turn_id="anchor-init",
        source_text="initialize canonical user anchor",
    )


def _node(repo: GraphRepository, user_id: str, name: str, turn: str) -> dict:
    return repo.create_node(
        user_id=user_id,
        name=name,
        turn_id=turn,
        source_role="user",
        source_text=name,
    )


def _edge(
    repo: GraphRepository,
    user_id: str,
    subject_node_id: int,
    relation: str,
    object_node_id: int,
    turn: str,
) -> dict:
    return repo.create_or_reinforce_edge(
        user_id=user_id,
        subject_node_id=subject_node_id,
        relation=relation,
        object_node_id=object_node_id,
        turn_id=turn,
        source_role="turn",
        source_text=f"{subject_node_id}:{relation}:{object_node_id}",
    )


def test_recall_returns_only_direct_neighbors(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "graph.db")
    try:
        _anchor(repo, "owner")
        a = _node(repo, "owner", "A", "t1")
        b = _node(repo, "owner", "B", "t1")
        c = _node(repo, "owner", "C", "t1")
        ab = _edge(repo, "owner", a["node_id"], "ab", b["node_id"], "t1")
        bc = _edge(repo, "owner", b["node_id"], "bc", c["node_id"], "t1")

        recalled = GraphRecallService(repo).recall_one_depth(
            user_id="owner",
            focus_node_id=a["node_id"],
        )

        assert recalled["depth"] == 1
        assert recalled["focus_node_id"] == a["node_id"]
        assert {node["node_id"] for node in recalled["nodes"]} == {a["node_id"], b["node_id"]}
        assert {edge["edge_id"] for edge in recalled["edges"]} == {ab["edge_id"]}
        assert c["node_id"] not in {node["node_id"] for node in recalled["nodes"]}
        assert bc["edge_id"] not in {edge["edge_id"] for edge in recalled["edges"]}
        assert recalled["origin_path"]["available"] is False
    finally:
        repo.close()


def test_recall_includes_incoming_and_outgoing_edges(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "graph.db")
    try:
        _anchor(repo, "owner")
        a = _node(repo, "owner", "A", "t1")
        b = _node(repo, "owner", "B", "t1")
        c = _node(repo, "owner", "C", "t1")
        ab = _edge(repo, "owner", a["node_id"], "ab", b["node_id"], "t1")
        bc = _edge(repo, "owner", b["node_id"], "bc", c["node_id"], "t1")

        recalled = GraphRecallService(repo).recall_one_depth(
            user_id="owner",
            focus_node_id=b["node_id"],
        )

        assert {node["node_id"] for node in recalled["nodes"]} == {
            a["node_id"],
            b["node_id"],
            c["node_id"],
        }
        assert {edge["edge_id"] for edge in recalled["edges"]} == {ab["edge_id"], bc["edge_id"]}
    finally:
        repo.close()


def test_isolated_focus_returns_only_focus_node(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "graph.db")
    try:
        _anchor(repo, "owner")
        focus = _node(repo, "owner", "isolated", "t1")
        recalled = GraphRecallService(repo).recall_one_depth(
            user_id="owner",
            focus_node_id=focus["node_id"],
        )
        assert recalled["nodes"] == [repo.get_node(user_id="owner", node_id=focus["node_id"])]
        assert recalled["edges"] == []
        assert recalled["origin_path"]["available"] is False
    finally:
        repo.close()


def test_recall_rejects_foreign_focus_node(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "graph.db")
    try:
        _anchor(repo, "owner")
        _anchor(repo, "other")
        foreign = _node(repo, "other", "private", "t1")
        with pytest.raises(GraphScopeError):
            GraphRecallService(repo).recall_one_depth(
                user_id="owner",
                focus_node_id=foreign["node_id"],
            )
    finally:
        repo.close()
