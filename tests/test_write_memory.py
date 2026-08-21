from __future__ import annotations

import pytest

from mai.graph import GraphRepository, GraphScopeError
from mai.memory_write import MemoryTurnScope, WriteMemoryTool, write_memory_schema
from mai.model import ModelContractError


def _node(repo: GraphRepository, name: str) -> dict:
    return repo.create_node(
        user_id="owner",
        name=name,
        turn_id="seed",
        source_role="user",
        source_text=name,
    )


def _scope(*node_ids: int) -> MemoryTurnScope:
    return MemoryTurnScope(
        user_id="owner",
        turn_id="turn-1",
        user_text="나는 MAI를 계속 개발하고 있어.",
        assistant_text="MAI 프로젝트를 계속 개발 중이라고 기억할게.",
        recalled_node_ids=frozenset(node_ids),
    )


def test_schema_exposes_only_recalled_existing_node_ids() -> None:
    schema = write_memory_schema([9, 3, 9])
    subject_variants = schema["properties"]["arguments"]["properties"]["subject"]["oneOf"]
    existing = next(item for item in subject_variants if "existing_node_id" in item.get("required", []))
    assert existing["properties"]["existing_node_id"]["enum"] == [3, 9]


def test_write_from_user_anchor_to_new_node_is_atomic_and_provenanced(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    try:
        anchor = repo.ensure_user_anchor(user_id="owner", turn_id="seed", source_text="owner")
        result = WriteMemoryTool(repo).execute(
            arguments={
                "subject": {"kind": "user"},
                "relation": "개발한다",
                "object": {"new_node": {"name": "MAI"}},
            },
            scope=_scope(),
        )

        assert result["status"] == "written"
        assert result["edge"]["subject_node_id"] == anchor["node_id"]
        assert result["edge"]["relation"] == "개발한다"
        assert len(result["created_nodes"]) == 1
        assert result["created_nodes"][0]["name"] == "MAI"
        assert result["edge"]["object_node_id"] == result["created_nodes"][0]["node_id"]

        provenance = repo.provenance_for_turn(user_id="owner", turn_id="turn-1")
        assert len(provenance) == 2
        assert {row["source_role"] for row in provenance} == {"turn"}
        assert all("나는 MAI를 계속 개발하고 있어." in row["source_text"] for row in provenance)
        assert all("MAI 프로젝트를 계속 개발 중" in row["source_text"] for row in provenance)
    finally:
        repo.close()


def test_write_can_use_node_actually_recalled_this_turn(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    try:
        repo.ensure_user_anchor(user_id="owner", turn_id="seed", source_text="owner")
        project = _node(repo, "MAI project")
        result = WriteMemoryTool(repo).execute(
            arguments={
                "subject": {"existing_node_id": project["node_id"]},
                "relation": "사용한다",
                "object": {"new_node": {"name": "semantic graph"}},
            },
            scope=_scope(project["node_id"]),
        )
        assert result["edge"]["subject_node_id"] == project["node_id"]
    finally:
        repo.close()


def test_lookup_only_or_other_existing_node_is_not_write_scope(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    try:
        repo.ensure_user_anchor(user_id="owner", turn_id="seed", source_text="owner")
        recalled = _node(repo, "recalled")
        not_recalled = _node(repo, "lookup candidate only")

        with pytest.raises(GraphScopeError):
            WriteMemoryTool(repo).execute(
                arguments={
                    "subject": {"existing_node_id": not_recalled["node_id"]},
                    "relation": "rel",
                    "object": {"existing_node_id": recalled["node_id"]},
                },
                scope=_scope(recalled["node_id"]),
            )
    finally:
        repo.close()


def test_scope_can_be_built_from_one_hop_and_origin_path() -> None:
    recall = {
        "nodes": [{"node_id": 7}, {"node_id": 8}],
        "origin_path": {"nodes": [{"node_id": 8}, {"node_id": 3}, {"node_id": 1}]},
    }
    scope = MemoryTurnScope.from_recall(
        user_id="owner",
        turn_id="t",
        user_text="u",
        assistant_text="a",
        recall_result=recall,
    )
    assert scope.recalled_node_ids == frozenset({1, 3, 7, 8})


def test_reinforcing_same_relation_increments_support_count(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    try:
        anchor = repo.ensure_user_anchor(user_id="owner", turn_id="seed", source_text="owner")
        project = _node(repo, "MAI")
        tool = WriteMemoryTool(repo)
        arguments = {
            "subject": {"kind": "user"},
            "relation": "개발한다",
            "object": {"existing_node_id": project["node_id"]},
        }
        first = tool.execute(arguments=arguments, scope=_scope(project["node_id"]))
        second = tool.execute(arguments=arguments, scope=_scope(project["node_id"]))
        assert first["edge"]["edge_id"] == second["edge"]["edge_id"]
        assert second["edge"]["support_count"] == 2
        assert second["edge"]["subject_node_id"] == anchor["node_id"]
    finally:
        repo.close()


def test_failure_after_new_subject_creation_rolls_back_entire_mutation(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    try:
        repo.ensure_user_anchor(user_id="owner", turn_id="seed", source_text="owner")
        tool = WriteMemoryTool(repo)

        with pytest.raises(ModelContractError):
            tool.execute(
                arguments={
                    "subject": {"new_node": {"name": "must rollback"}},
                    "relation": "rel",
                    "object": {"bad": "endpoint"},
                },
                scope=_scope(),
            )

        lookup = repo.lookup_nodes(user_id="owner", queries=["must rollback"])
        assert lookup["matches"] == []
        assert repo.provenance_for_turn(user_id="owner", turn_id="turn-1") == []
    finally:
        repo.close()


def test_write_requires_fixed_answer_context(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    try:
        repo.ensure_user_anchor(user_id="owner", turn_id="seed", source_text="owner")
        scope = MemoryTurnScope(
            user_id="owner",
            turn_id="turn-1",
            user_text="hello",
            assistant_text="",
            recalled_node_ids=frozenset(),
        )
        with pytest.raises(ValueError):
            WriteMemoryTool(repo).execute(
                arguments={
                    "subject": {"kind": "user"},
                    "relation": "말했다",
                    "object": {"new_node": {"name": "hello"}},
                },
                scope=scope,
            )
    finally:
        repo.close()
