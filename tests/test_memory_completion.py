from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from mai.graph import GraphRepository
from mai.memory_completion import MandatoryMemoryCompletion
from mai.memory_revise import ReviseMemoryTool
from mai.memory_write import MemoryTurnScope, WriteMemoryTool
from mai.model import ModelContractError


@dataclass
class FakeModel:
    actions: list[dict]
    schemas: list[dict] = field(default_factory=list)

    def structured(self, *, messages: list[dict[str, str]], schema: dict) -> dict:
        self.schemas.append(schema)
        if not self.actions:
            raise AssertionError("unexpected model round")
        return self.actions.pop(0)


def _turn(*, recalled: set[int] | None = None, answer: str = "고정 답변") -> MemoryTurnScope:
    return MemoryTurnScope(
        user_id="owner",
        turn_id="turn-1",
        user_text="사용자 입력",
        assistant_text=answer,
        recalled_node_ids=frozenset(recalled or set()),
    )


def _runner(repo: GraphRepository, model: FakeModel) -> MandatoryMemoryCompletion:
    return MandatoryMemoryCompletion(model, WriteMemoryTool(repo), ReviseMemoryTool(repo))


def _variants(schema: dict) -> list[dict]:
    return schema.get("oneOf", [schema])


def _has_done(schema: dict) -> bool:
    return any(v.get("properties", {}).get("action") == {"const": "done"} for v in _variants(schema))


def _tool_names(schema: dict) -> set[str]:
    names: set[str] = set()
    for variant in _variants(schema):
        tool = variant.get("properties", {}).get("tool", {}).get("const")
        if tool:
            names.add(str(tool))
    return names


def test_done_is_absent_before_first_successful_mutation(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    try:
        repo.ensure_user_anchor(user_id="owner", turn_id="seed", source_text="owner")
        model = FakeModel([
            {
                "action": "tool",
                "tool": "write_memory",
                "arguments": {
                    "subject": {"kind": "user"},
                    "relation": "likes",
                    "object": {"new_node": {"name": "A"}},
                },
            },
            {"action": "done"},
        ])
        result = _runner(repo, model).run(turn=_turn(), recall_result=None)

        assert result["status"] == "done"
        assert result["mutation_count"] == 1
        assert _has_done(model.schemas[0]) is False
        assert _has_done(model.schemas[1]) is True
    finally:
        repo.close()


def test_premature_done_fails_visibly(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    try:
        repo.ensure_user_anchor(user_id="owner", turn_id="seed", source_text="owner")
        model = FakeModel([{"action": "done"}])
        with pytest.raises(ModelContractError):
            _runner(repo, model).run(turn=_turn(), recall_result=None)
        assert _has_done(model.schemas[0]) is False
    finally:
        repo.close()


def test_created_node_enters_scope_for_later_write(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    try:
        repo.ensure_user_anchor(user_id="owner", turn_id="seed", source_text="owner")
        first_action = {
            "action": "tool",
            "tool": "write_memory",
            "arguments": {
                "subject": {"kind": "user"},
                "relation": "has",
                "object": {"new_node": {"name": "Project"}},
            },
        }

        class ReusingModel(FakeModel):
            def structured(self, *, messages: list[dict[str, str]], schema: dict) -> dict:
                self.schemas.append(schema)
                if len(self.schemas) == 1:
                    return first_action
                if len(self.schemas) == 2:
                    variants = _variants(schema)
                    write_variant = next(v for v in variants if v.get("properties", {}).get("tool") == {"const": "write_memory"})
                    endpoint = write_variant["properties"]["arguments"]["properties"]["subject"]["oneOf"]
                    existing = next(v for v in endpoint if "existing_node_id" in v.get("properties", {}))
                    created_id = existing["properties"]["existing_node_id"]["enum"][0]
                    return {
                        "action": "tool",
                        "tool": "write_memory",
                        "arguments": {
                            "subject": {"existing_node_id": created_id},
                            "relation": "contains",
                            "object": {"new_node": {"name": "MAI"}},
                        },
                    }
                return {"action": "done"}

        model = ReusingModel([])
        result = _runner(repo, model).run(turn=_turn(), recall_result=None)
        assert result["mutation_count"] == 2
        assert result["mutations"][1]["edge"]["relation"] == "contains"
    finally:
        repo.close()


def test_revise_is_exposed_only_when_an_edge_is_eligible(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    try:
        anchor = repo.ensure_user_anchor(user_id="owner", turn_id="seed", source_text="owner")
        a = repo.create_node(
            user_id="owner", name="A", turn_id="seed", source_role="user", source_text="A"
        )
        edge = repo.create_or_reinforce_edge(
            user_id="owner",
            subject_node_id=anchor["node_id"],
            relation="old",
            object_node_id=a["node_id"],
            turn_id="seed",
            source_role="turn",
            source_text="old",
        )
        recall = {
            "nodes": [anchor, a],
            "edges": [edge],
            "origin_path": {"nodes": [anchor, a], "edges": [edge]},
        }
        model = FakeModel([
            {
                "action": "tool",
                "tool": "revise_memory",
                "arguments": {
                    "edge_id": edge["edge_id"],
                    "subject": {"kind": "user"},
                    "relation": "new",
                    "object": {"existing_node_id": a["node_id"]},
                },
            },
            {"action": "done"},
        ])
        result = _runner(repo, model).run(
            turn=_turn(recalled={anchor["node_id"], a["node_id"]}),
            recall_result=recall,
        )
        assert "revise_memory" in _tool_names(model.schemas[0])
        assert _has_done(model.schemas[0]) is False
        assert result["mutations"][0]["status"] == "revised"
        assert result["mutations"][0]["edge"]["relation"] == "new"
    finally:
        repo.close()


def test_revise_is_absent_without_eligible_edge(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    try:
        repo.ensure_user_anchor(user_id="owner", turn_id="seed", source_text="owner")
        model = FakeModel([
            {
                "action": "tool",
                "tool": "write_memory",
                "arguments": {
                    "subject": {"kind": "user"},
                    "relation": "r",
                    "object": {"new_node": {"name": "A"}},
                },
            },
            {"action": "done"},
        ])
        _runner(repo, model).run(turn=_turn(), recall_result=None)
        assert _tool_names(model.schemas[0]) == {"write_memory"}
    finally:
        repo.close()


def test_current_turn_written_edge_becomes_revisable(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    try:
        repo.ensure_user_anchor(user_id="owner", turn_id="seed", source_text="owner")

        class WriteThenReviseModel(FakeModel):
            def structured(self, *, messages: list[dict[str, str]], schema: dict) -> dict:
                self.schemas.append(schema)
                if len(self.schemas) == 1:
                    return {
                        "action": "tool",
                        "tool": "write_memory",
                        "arguments": {
                            "subject": {"kind": "user"},
                            "relation": "old",
                            "object": {"new_node": {"name": "A"}},
                        },
                    }
                if len(self.schemas) == 2:
                    revise_variant = next(v for v in _variants(schema) if v.get("properties", {}).get("tool") == {"const": "revise_memory"})
                    args = revise_variant["properties"]["arguments"]["properties"]
                    edge_id = args["edge_id"]["enum"][0]
                    endpoint = args["object"]["oneOf"]
                    existing = next(v for v in endpoint if "existing_node_id" in v.get("properties", {}))
                    node_id = existing["properties"]["existing_node_id"]["enum"][0]
                    return {
                        "action": "tool",
                        "tool": "revise_memory",
                        "arguments": {
                            "edge_id": edge_id,
                            "subject": {"kind": "user"},
                            "relation": "new",
                            "object": {"existing_node_id": node_id},
                        },
                    }
                return {"action": "done"}

        model = WriteThenReviseModel([])
        result = _runner(repo, model).run(turn=_turn(), recall_result=None)
        assert result["mutation_count"] == 2
        assert result["mutations"][1]["status"] == "revised"
        assert result["mutations"][1]["edge"]["relation"] == "new"
    finally:
        repo.close()


def test_multiple_mutations_have_no_framework_round_cap(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    try:
        repo.ensure_user_anchor(user_id="owner", turn_id="seed", source_text="owner")
        actions = []
        for i in range(5):
            actions.append(
                {
                    "action": "tool",
                    "tool": "write_memory",
                    "arguments": {
                        "subject": {"kind": "user"},
                        "relation": f"r{i}",
                        "object": {"new_node": {"name": f"N{i}"}},
                    },
                }
            )
        actions.append({"action": "done"})
        model = FakeModel(actions)
        result = _runner(repo, model).run(turn=_turn(), recall_result=None)
        assert result["mutation_count"] == 5
        assert len(model.schemas) == 6
    finally:
        repo.close()


def test_fixed_answer_is_required_before_memory_completion(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    try:
        model = FakeModel([])
        with pytest.raises(ValueError):
            _runner(repo, model).run(turn=_turn(answer=""), recall_result=None)
        assert model.schemas == []
    finally:
        repo.close()
