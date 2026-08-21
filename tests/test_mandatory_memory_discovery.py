from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from mai.graph import GraphDiscoveryService, GraphRecallService, GraphRepository
from mai.memory_discovery import MandatoryMemoryDiscovery
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


def _node(repo: GraphRepository, user: str, name: str) -> dict:
    return repo.create_node(
        user_id=user,
        name=name,
        turn_id="seed",
        source_role="user",
        source_text=name,
    )


def _edge(repo: GraphRepository, user: str, a: int, relation: str, b: int) -> None:
    repo.create_or_reinforce_edge(
        user_id=user,
        subject_node_id=a,
        relation=relation,
        object_node_id=b,
        turn_id="seed",
        source_role="turn",
        source_text=relation,
    )


def _services(repo: GraphRepository, model: FakeModel) -> MandatoryMemoryDiscovery:
    return MandatoryMemoryDiscovery(model, GraphDiscoveryService(repo), GraphRecallService(repo))


def test_first_round_schema_allows_only_node_lookup(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    try:
        model = FakeModel([{"action": "tool", "tool": "node_lookup", "arguments": {"queries": ["missing"]}}])
        result = _services(repo, model).run(user_id="owner", user_text="hello")
        assert result["status"] == "no_match"
        schema = model.schemas[0]
        assert schema["properties"]["tool"] == {"const": "node_lookup"}
        assert schema["properties"]["arguments"]["properties"]["queries"]["maxItems"] == 3
    finally:
        repo.close()


def test_lookup_then_recall_uses_actual_candidate_enum(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    try:
        anchor = repo.ensure_user_anchor(user_id="owner", turn_id="seed", source_text="owner")
        project = _node(repo, "owner", "MAI project")
        _edge(repo, "owner", anchor["node_id"], "has project", project["node_id"])
        model = FakeModel([
            {"action": "tool", "tool": "node_lookup", "arguments": {"queries": ["MAI"]}},
            {"action": "tool", "tool": "recall_memory", "arguments": {"focus_node_id": project["node_id"]}},
        ])
        result = _services(repo, model).run(user_id="owner", user_text="MAI?")
        assert result["status"] == "recalled"
        assert result["recall"]["focus_node_id"] == project["node_id"]
        assert result["recall"]["origin_path"]["available"] is True
        selection_schema = model.schemas[1]
        recall_variant = selection_schema["oneOf"][0]
        assert recall_variant["properties"]["arguments"]["properties"]["focus_node_id"]["enum"] == [project["node_id"]]
    finally:
        repo.close()


def test_model_cannot_skip_first_lookup(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    try:
        model = FakeModel([{"action": "tool", "tool": "recall_memory", "arguments": {"focus_node_id": 1}}])
        with pytest.raises(ModelContractError):
            _services(repo, model).run(user_id="owner", user_text="x")
    finally:
        repo.close()


def test_focus_outside_lookup_scope_fails(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    try:
        repo.ensure_user_anchor(user_id="owner", turn_id="seed", source_text="owner")
        project = _node(repo, "owner", "MAI")
        other = _node(repo, "owner", "unrelated")
        model = FakeModel([
            {"action": "tool", "tool": "node_lookup", "arguments": {"queries": ["MAI"]}},
            {"action": "tool", "tool": "recall_memory", "arguments": {"focus_node_id": other["node_id"]}},
        ])
        with pytest.raises(ModelContractError):
            _services(repo, model).run(user_id="owner", user_text="MAI")
        assert project["node_id"] != other["node_id"]
    finally:
        repo.close()


def test_model_can_lookup_again_before_recall(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    try:
        anchor = repo.ensure_user_anchor(user_id="owner", turn_id="seed", source_text="owner")
        a = _node(repo, "owner", "project")
        b = _node(repo, "owner", "MAI")
        _edge(repo, "owner", anchor["node_id"], "has", a["node_id"])
        _edge(repo, "owner", a["node_id"], "contains", b["node_id"])
        model = FakeModel([
            {"action": "tool", "tool": "node_lookup", "arguments": {"queries": ["project"]}},
            {"action": "tool", "tool": "node_lookup", "arguments": {"queries": ["MAI"]}},
            {"action": "tool", "tool": "recall_memory", "arguments": {"focus_node_id": b["node_id"]}},
        ])
        result = _services(repo, model).run(user_id="owner", user_text="MAI")
        assert result["status"] == "recalled"
        assert result["recall"]["focus_node_id"] == b["node_id"]
        assert len(model.schemas) == 3
    finally:
        repo.close()
