from __future__ import annotations

import pytest

from mai.graph import GraphDiscoveryService, GraphRecallService, GraphRepository, GraphScopeError


def _node(repo: GraphRepository, user_id: str, name: str, turn: str = "t1") -> dict:
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
    subject: dict,
    relation: str,
    object_: dict,
    turn: str = "t1",
) -> dict:
    return repo.create_or_reinforce_edge(
        user_id=user_id,
        subject_node_id=subject["node_id"],
        relation=relation,
        object_node_id=object_["node_id"],
        turn_id=turn,
        source_role="turn",
        source_text=f"{subject['node_id']}:{relation}:{object_['node_id']}",
    )


def _anchor(repo: GraphRepository, user_id: str = "owner") -> dict:
    return repo.ensure_user_anchor(
        user_id=user_id,
        turn_id="anchor-init",
        source_text="initialize canonical user anchor",
    )


def test_user_anchor_is_single_stable_framework_managed_node(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "graph.db")
    try:
        first = _anchor(repo)
        second = repo.ensure_user_anchor(
            user_id="owner",
            turn_id="later",
            source_text="should not create another anchor",
        )

        assert first["node_id"] == second["node_id"]
        assert repo.get_user_anchor(user_id="owner")["node_id"] == first["node_id"]

        provenance = repo.provenance_for_turn(user_id="owner", turn_id="anchor-init")
        assert len(provenance) == 1
        assert provenance[0]["node_id"] == first["node_id"]
        assert repo.provenance_for_turn(user_id="owner", turn_id="later") == []

        with pytest.raises(GraphScopeError):
            repo.rename_node(
                user_id="owner",
                node_id=first["node_id"],
                name="renamed",
                turn_id="t2",
                source_role="turn",
                source_text="attempted rename",
            )
    finally:
        repo.close()


def test_node_lookup_is_partial_lexical_owned_and_paginated(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "graph.db")
    try:
        _anchor(repo)
        first = _node(repo, "owner", "MAI")
        second = _node(repo, "owner", "MAI 프로젝트")
        third = _node(repo, "owner", "MAI 그래프")
        _node(repo, "owner", "다른 개념")
        _node(repo, "other", "MAI private")

        service = GraphDiscoveryService(repo)
        page1 = service.node_lookup(user_id="owner", queries=["MAI"], limit=2)
        assert [item["node_id"] for item in page1["matches"]] == [first["node_id"], second["node_id"]]
        assert page1["has_more"] is True
        assert page1["next_cursor"] == second["node_id"]

        page2 = service.node_lookup(
            user_id="owner",
            queries=["MAI"],
            after_node_id=page1["next_cursor"],
            limit=2,
        )
        assert [item["node_id"] for item in page2["matches"]] == [third["node_id"]]
        assert page2["has_more"] is False
        assert page2["next_cursor"] is None

        assert all(item["user_id"] == "owner" for item in page1["matches"] + page2["matches"])
    finally:
        repo.close()


def test_node_lookup_accepts_at_most_three_model_queries(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "graph.db")
    try:
        service = GraphDiscoveryService(repo)
        with pytest.raises(ValueError):
            service.node_lookup(user_id="owner", queries=[])
        with pytest.raises(ValueError):
            service.node_lookup(user_id="owner", queries=["a", "b", "c", "d"])
    finally:
        repo.close()


def test_origin_path_returns_shortest_path_and_preserves_edge_direction(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "graph.db")
    try:
        anchor = _anchor(repo)
        project = _node(repo, "owner", "프로젝트")
        sllm = _node(repo, "owner", "sLLM")
        mai = _node(repo, "owner", "MAI")
        detour = _node(repo, "owner", "우회")

        user_project = _edge(repo, "owner", anchor, "한다", project)
        project_sllm = _edge(repo, "owner", sllm, "속한다", project)
        sllm_mai = _edge(repo, "owner", sllm, "발전한다", mai)
        _edge(repo, "owner", mai, "우회한다", detour)
        _edge(repo, "owner", detour, "돌아간다", project)

        recalled = GraphRecallService(repo).recall_one_depth(
            user_id="owner",
            focus_node_id=mai["node_id"],
        )
        origin = recalled["origin_path"]

        assert origin["available"] is True
        assert [node["node_id"] for node in origin["nodes"]] == [
            mai["node_id"],
            sllm["node_id"],
            project["node_id"],
            anchor["node_id"],
        ]
        assert [edge["edge_id"] for edge in origin["edges"]] == [
            sllm_mai["edge_id"],
            project_sllm["edge_id"],
            user_project["edge_id"],
        ]
        assert origin["edges"][0]["subject_node_id"] == sllm["node_id"]
        assert origin["edges"][0]["object_node_id"] == mai["node_id"]
        assert origin["edges"][1]["subject_node_id"] == sllm["node_id"]
        assert origin["edges"][1]["object_node_id"] == project["node_id"]
    finally:
        repo.close()


def test_equal_shortest_origin_paths_use_stable_edge_order(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "graph.db")
    try:
        anchor = _anchor(repo)
        left = _node(repo, "owner", "left")
        right = _node(repo, "owner", "right")
        focus = _node(repo, "owner", "focus")

        left_focus = _edge(repo, "owner", left, "left-focus", focus)
        _edge(repo, "owner", anchor, "anchor-left", left)
        _edge(repo, "owner", right, "right-focus", focus)
        _edge(repo, "owner", anchor, "anchor-right", right)

        origin = repo.origin_path_to_user_anchor(user_id="owner", focus_node_id=focus["node_id"])
        assert origin["available"] is True
        assert origin["nodes"][1]["node_id"] == left["node_id"]
        assert origin["edges"][0]["edge_id"] == left_focus["edge_id"]
    finally:
        repo.close()


def test_origin_path_is_explicitly_unavailable_when_disconnected(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "graph.db")
    try:
        anchor = _anchor(repo)
        focus = _node(repo, "owner", "isolated")

        origin = repo.origin_path_to_user_anchor(user_id="owner", focus_node_id=focus["node_id"])
        assert origin == {
            "available": False,
            "focus_node_id": focus["node_id"],
            "anchor_node_id": anchor["node_id"],
            "reason": "disconnected",
            "nodes": [],
            "edges": [],
        }
    finally:
        repo.close()
