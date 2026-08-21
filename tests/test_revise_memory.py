from __future__ import annotations

import sqlite3

import pytest

from mai.graph import GraphRepository, GraphScopeError
from mai.memory_revise import MemoryScopeError, ReviseMemoryScope, ReviseMemoryTool
from mai.memory_write import MemoryTurnScope, WriteMemoryTool


def _node(repo: GraphRepository, user: str, name: str) -> dict:
    return repo.create_node(
        user_id=user,
        name=name,
        turn_id="seed",
        source_role="user",
        source_text=name,
    )


def _edge(repo: GraphRepository, user: str, subject: int, relation: str, obj: int) -> dict:
    return repo.create_or_reinforce_edge(
        user_id=user,
        subject_node_id=subject,
        relation=relation,
        object_node_id=obj,
        turn_id="seed",
        source_role="turn",
        source_text=relation,
    )


def _turn(user: str = "owner", recalled: set[int] | None = None) -> MemoryTurnScope:
    return MemoryTurnScope(
        user_id=user,
        turn_id="turn-1",
        user_text="새 정보",
        assistant_text="고정 답변",
        recalled_node_ids=frozenset(recalled or set()),
    )


def _scope(turn: MemoryTurnScope, recall: dict | None, writes: list[dict] | None = None) -> ReviseMemoryScope:
    return ReviseMemoryScope.from_turn(turn=turn, recall_result=recall, write_results=writes or [])


def test_schema_exposes_only_eligible_ids(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    try:
        anchor = repo.ensure_user_anchor(user_id="owner", turn_id="seed", source_text="owner")
        a = _node(repo, "owner", "A")
        b = _node(repo, "owner", "B")
        edge = _edge(repo, "owner", anchor["node_id"], "has", a["node_id"])
        turn = _turn(recalled={anchor["node_id"], a["node_id"]})
        recall = {"nodes": [anchor, a], "edges": [edge], "origin_path": {"nodes": [anchor, a], "edges": [edge]}}
        scope = _scope(turn, recall)
        schema = ReviseMemoryTool(repo).schema(scope=scope)

        args = schema["properties"]["arguments"]["properties"]
        assert args["edge_id"]["enum"] == [edge["edge_id"]]
        endpoint = args["subject"]["oneOf"]
        existing = [v for v in endpoint if "existing_node_id" in v.get("properties", {})][0]
        assert existing["properties"]["existing_node_id"]["enum"] == sorted({anchor["node_id"], a["node_id"]})
        assert b["node_id"] not in existing["properties"]["existing_node_id"]["enum"]
    finally:
        repo.close()


def test_revise_recalled_edge_relation(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    try:
        anchor = repo.ensure_user_anchor(user_id="owner", turn_id="seed", source_text="owner")
        a = _node(repo, "owner", "A")
        edge = _edge(repo, "owner", anchor["node_id"], "old", a["node_id"])
        recall = {"nodes": [anchor, a], "edges": [edge], "origin_path": {"nodes": [anchor, a], "edges": [edge]}}
        scope = _scope(_turn(recalled={anchor["node_id"], a["node_id"]}), recall)

        result = ReviseMemoryTool(repo).execute(
            arguments={
                "edge_id": edge["edge_id"],
                "subject": {"kind": "user"},
                "relation": "new",
                "object": {"existing_node_id": a["node_id"]},
            },
            scope=scope,
        )
        assert result["status"] == "revised"
        assert result["edge"]["relation"] == "new"
    finally:
        repo.close()


def test_revise_can_use_current_turn_created_node_and_edge(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    try:
        repo.ensure_user_anchor(user_id="owner", turn_id="seed", source_text="owner")
        turn = _turn()
        write = WriteMemoryTool(repo).execute(
            arguments={
                "subject": {"kind": "user"},
                "relation": "has",
                "object": {"new_node": {"name": "A"}},
            },
            scope=turn,
        )
        created = write["created_nodes"][0]
        scope = _scope(turn, None, [write])

        result = ReviseMemoryTool(repo).execute(
            arguments={
                "edge_id": write["edge"]["edge_id"],
                "subject": {"kind": "user"},
                "relation": "owns",
                "object": {"existing_node_id": created["node_id"]},
            },
            scope=scope,
        )
        assert result["edge"]["relation"] == "owns"
    finally:
        repo.close()


def test_unrecalled_edge_is_rejected(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    try:
        anchor = repo.ensure_user_anchor(user_id="owner", turn_id="seed", source_text="owner")
        a = _node(repo, "owner", "A")
        edge = _edge(repo, "owner", anchor["node_id"], "has", a["node_id"])
        scope = _scope(_turn(recalled={anchor["node_id"], a["node_id"]}), {"nodes": [anchor, a], "edges": [], "origin_path": {"nodes": [], "edges": []}})
        with pytest.raises(MemoryScopeError):
            ReviseMemoryTool(repo).execute(
                arguments={
                    "edge_id": edge["edge_id"],
                    "subject": {"kind": "user"},
                    "relation": "changed",
                    "object": {"existing_node_id": a["node_id"]},
                },
                scope=scope,
            )
    finally:
        repo.close()


def test_unrecalled_existing_node_is_rejected(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    try:
        anchor = repo.ensure_user_anchor(user_id="owner", turn_id="seed", source_text="owner")
        a = _node(repo, "owner", "A")
        b = _node(repo, "owner", "B")
        edge = _edge(repo, "owner", anchor["node_id"], "has", a["node_id"])
        recall = {"nodes": [anchor, a], "edges": [edge], "origin_path": {"nodes": [anchor, a], "edges": [edge]}}
        scope = _scope(_turn(recalled={anchor["node_id"], a["node_id"]}), recall)
        with pytest.raises(MemoryScopeError):
            ReviseMemoryTool(repo).execute(
                arguments={
                    "edge_id": edge["edge_id"],
                    "subject": {"kind": "user"},
                    "relation": "changed",
                    "object": {"existing_node_id": b["node_id"]},
                },
                scope=scope,
            )
    finally:
        repo.close()


def test_foreign_owned_edge_fails_even_if_scope_is_forged(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    try:
        repo.ensure_user_anchor(user_id="owner", turn_id="seed", source_text="owner")
        foreign_anchor = repo.ensure_user_anchor(user_id="other", turn_id="seed", source_text="other")
        foreign_node = _node(repo, "other", "foreign")
        foreign_edge = _edge(repo, "other", foreign_anchor["node_id"], "has", foreign_node["node_id"])
        scope = ReviseMemoryScope(
            turn=_turn(),
            eligible_node_ids=frozenset(),
            eligible_edge_ids=frozenset({foreign_edge["edge_id"]}),
        )
        with pytest.raises(GraphScopeError):
            ReviseMemoryTool(repo).execute(
                arguments={
                    "edge_id": foreign_edge["edge_id"],
                    "subject": {"kind": "user"},
                    "relation": "x",
                    "object": {"new_node": {"name": "X"}},
                },
                scope=scope,
            )
    finally:
        repo.close()


def test_duplicate_edge_collision_remains_visible(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    try:
        anchor = repo.ensure_user_anchor(user_id="owner", turn_id="seed", source_text="owner")
        a = _node(repo, "owner", "A")
        b = _node(repo, "owner", "B")
        edge1 = _edge(repo, "owner", anchor["node_id"], "r1", a["node_id"])
        _edge(repo, "owner", anchor["node_id"], "r2", b["node_id"])
        recall = {"nodes": [anchor, a, b], "edges": [edge1], "origin_path": {"nodes": [anchor, a], "edges": [edge1]}}
        scope = _scope(_turn(recalled={anchor["node_id"], a["node_id"], b["node_id"]}), recall)
        with pytest.raises(sqlite3.IntegrityError):
            ReviseMemoryTool(repo).execute(
                arguments={
                    "edge_id": edge1["edge_id"],
                    "subject": {"kind": "user"},
                    "relation": "r2",
                    "object": {"existing_node_id": b["node_id"]},
                },
                scope=scope,
            )
    finally:
        repo.close()


def test_new_node_rolls_back_when_revision_collides(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    try:
        anchor = repo.ensure_user_anchor(user_id="owner", turn_id="seed", source_text="owner")
        a = _node(repo, "owner", "A")
        b = _node(repo, "owner", "B")
        edge1 = _edge(repo, "owner", anchor["node_id"], "r1", a["node_id"])
        _edge(repo, "owner", anchor["node_id"], "r2", b["node_id"])
        recall = {"nodes": [anchor, a, b], "edges": [edge1], "origin_path": {"nodes": [anchor, a], "edges": [edge1]}}
        scope = _scope(_turn(recalled={anchor["node_id"], a["node_id"], b["node_id"]}), recall)
        before = repo.lookup_nodes(user_id="owner", queries=["temp"])["matches"]
        assert before == []

        with pytest.raises(sqlite3.IntegrityError):
            ReviseMemoryTool(repo).execute(
                arguments={
                    "edge_id": edge1["edge_id"],
                    "subject": {"new_node": {"name": "temp"}},
                    "relation": "r2",
                    "object": {"existing_node_id": b["node_id"]},
                },
                scope=scope,
            )

        assert repo.lookup_nodes(user_id="owner", queries=["temp"])["matches"] == []
    finally:
        repo.close()


def test_revision_records_turn_provenance(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    try:
        anchor = repo.ensure_user_anchor(user_id="owner", turn_id="seed", source_text="owner")
        a = _node(repo, "owner", "A")
        edge = _edge(repo, "owner", anchor["node_id"], "old", a["node_id"])
        recall = {"nodes": [anchor, a], "edges": [edge], "origin_path": {"nodes": [anchor, a], "edges": [edge]}}
        scope = _scope(_turn(recalled={anchor["node_id"], a["node_id"]}), recall)
        result = ReviseMemoryTool(repo).execute(
            arguments={
                "edge_id": edge["edge_id"],
                "subject": {"kind": "user"},
                "relation": "new",
                "object": {"new_node": {"name": "C"}},
            },
            scope=scope,
        )
        provenance = repo.provenance_for_turn(user_id="owner", turn_id="turn-1")
        assert any(row["edge_id"] == edge["edge_id"] for row in provenance)
        created_id = result["created_nodes"][0]["node_id"]
        assert any(row["node_id"] == created_id for row in provenance)
        assert all("고정 답변" in row["source_text"] for row in provenance)
    finally:
        repo.close()
