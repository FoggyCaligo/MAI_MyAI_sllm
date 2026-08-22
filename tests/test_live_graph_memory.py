from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from mai.agent import AgentLifecycle
from mai.graph import GraphConflictError, GraphRepository, GraphSourceStore
from mai.memory_extension import AgentGraphMemoryExtension
from mai.model import ModelContractError


@dataclass
class FakeEmbedding:
    vectors: dict[str, list[float]]

    def embed(self, texts: list[str]) -> list[list[float]]:
        result = []
        for text in texts:
            if text not in self.vectors:
                raise AssertionError(f"missing fake embedding for {text!r}")
            result.append(list(self.vectors[text]))
        return result


@dataclass
class FakeModel:
    actions: list[dict]
    schemas: list[dict] = field(default_factory=list)
    messages: list[list[dict[str, str]]] = field(default_factory=list)

    def structured(self, *, messages: list[dict[str, str]], schema: dict) -> dict:
        self.schemas.append(schema)
        self.messages.append([dict(item) for item in messages])
        if not self.actions:
            raise AssertionError("unexpected model round")
        return self.actions.pop(0)


def _memory(repo, sources, vectors):
    return AgentGraphMemoryExtension(
        repository=repo,
        source_store=sources,
        embedding=FakeEmbedding(vectors),
        embedding_model_name="fake-embed",
    )


def _schema_variants(schema: dict) -> list[dict]:
    return list(schema.get("oneOf", [schema]))


def _has_answer(schema: dict) -> bool:
    for variant in _schema_variants(schema):
        action = (variant.get("properties") or {}).get("action") or {}
        if action.get("const") == "answer":
            return True
    return False


def test_query_recall_returns_candidates_without_opening_viewed_graph(tmp_path) -> None:
    path = tmp_path / "graph.db"
    repo = GraphRepository(path)
    sources = GraphSourceStore(path)
    try:
        node = repo.create_node(user_id="u", name="Mai")
        repo.set_node_embedding(user_id="u", node_id=node["node_id"], model="fake-embed", vector=[0.0, 1.0])
        memory = _memory(repo, sources, {"사용자": [1.0, 0.0], "Mai query": [0.0, 1.0]})
        state = memory.begin_turn(user_id="u", turn_id="t1", user_text="hello")

        assert memory.answer_schema(state) is None
        result = memory.execute(
            tool="memory/recall",
            arguments={"query": "Mai query"},
            state=state,
        )

        assert result["candidates"][0]["node_id"] == node["node_id"]
        assert result["viewed_graph"] == {"nodes": [], "edges": []}
        assert memory.answer_schema(state)["properties"]["graph_synced"] == {"const": True}
    finally:
        sources.close()
        repo.close()


def test_node_recall_accumulates_one_hop_into_turn_viewed_graph(tmp_path) -> None:
    path = tmp_path / "graph.db"
    repo = GraphRepository(path)
    sources = GraphSourceStore(path)
    try:
        a = repo.create_node(user_id="u", name="A")
        b = repo.create_node(user_id="u", name="B")
        c = repo.create_node(user_id="u", name="C")
        repo.set_node_embedding(user_id="u", node_id=a["node_id"], model="fake-embed", vector=[1.0, 0.0, 0.0])
        repo.set_node_embedding(user_id="u", node_id=b["node_id"], model="fake-embed", vector=[0.7, 0.3, 0.0])
        repo.set_node_embedding(user_id="u", node_id=c["node_id"], model="fake-embed", vector=[0.0, 0.0, 1.0])
        repo.create_edge(
            user_id="u",
            start_node_id=a["node_id"],
            end_node_id=b["node_id"],
            relation="A to B",
            weight=1.0,
            personal_relevance=0.5,
        )
        memory = _memory(
            repo,
            sources,
            {
                "사용자": [0.0, 1.0, 0.0],
                "find A": [1.0, 0.0, 0.0],
                "find C": [0.0, 0.0, 1.0],
            },
        )
        state = memory.begin_turn(user_id="u", turn_id="t1", user_text="hello")

        first = memory.execute(tool="memory/recall", arguments={"query": "find A"}, state=state)
        memory.execute(
            tool="memory/recall",
            arguments={"node_id": first["candidates"][0]["node_id"]},
            state=state,
        )
        second = memory.execute(tool="memory/recall", arguments={"query": "find C"}, state=state)
        opened = memory.execute(
            tool="memory/recall",
            arguments={"node_id": second["candidates"][0]["node_id"]},
            state=state,
        )

        names = {node["name"] for node in opened["viewed_graph"]["nodes"]}
        assert {"A", "B", "C"}.issubset(names)
    finally:
        sources.close()
        repo.close()


def test_each_new_node_requires_fresh_query_recall(tmp_path) -> None:
    path = tmp_path / "graph.db"
    repo = GraphRepository(path)
    sources = GraphSourceStore(path)
    try:
        memory = _memory(
            repo,
            sources,
            {
                "사용자": [1.0, 0.0],
                "first lookup": [0.0, 1.0],
                "first node": [0.0, 1.0],
            },
        )
        state = memory.begin_turn(user_id="u", turn_id="t1", user_text="source fact")
        source_id = next(iter(state.available_source_ids))
        memory.execute(tool="memory/recall", arguments={"query": "first lookup"}, state=state)
        memory.execute(
            tool="memory/generate/node",
            arguments={"kind": "concept", "name": "first node", "source_ids": [source_id]},
            state=state,
        )

        with pytest.raises(ModelContractError, match="fresh query recall"):
            memory.execute(
                tool="memory/generate/node",
                arguments={"kind": "concept", "name": "second node", "source_ids": [source_id]},
                state=state,
            )
    finally:
        sources.close()
        repo.close()


def test_edge_fix_applies_delta_promotes_relevance_and_disconnects(tmp_path) -> None:
    path = tmp_path / "graph.db"
    repo = GraphRepository(path)
    sources = GraphSourceStore(path)
    try:
        a = repo.create_node(user_id="u", name="A")
        b = repo.create_node(user_id="u", name="B")
        repo.set_node_embedding(user_id="u", node_id=a["node_id"], model="fake-embed", vector=[1.0, 0.0])
        repo.set_node_embedding(user_id="u", node_id=b["node_id"], model="fake-embed", vector=[0.9, 0.1])
        memory = _memory(repo, sources, {"사용자": [0.0, 1.0], "pair": [1.0, 0.0]})
        state = memory.begin_turn(user_id="u", turn_id="t1", user_text="A and B are related")
        source_id = next(iter(state.available_source_ids))
        candidates = memory.execute(tool="memory/recall", arguments={"query": "pair"}, state=state)["candidates"]
        for node_id in [a["node_id"], b["node_id"]]:
            assert node_id in {item["node_id"] for item in candidates}
            memory.execute(tool="memory/recall", arguments={"node_id": node_id}, state=state)

        created = memory.execute(
            tool="memory/generate/edge",
            arguments={
                "start_node_id": a["node_id"],
                "end_node_id": b["node_id"],
                "relation": "related",
                "weight": 0.7,
                "personal_relevance": "general_knowledge",
                "source_ids": [source_id],
            },
            state=state,
        )
        edge_id = created["edge"]["edge_id"]

        with pytest.raises(GraphConflictError):
            memory.execute(
                tool="memory/generate/edge",
                arguments={
                    "start_node_id": a["node_id"],
                    "end_node_id": b["node_id"],
                    "relation": "another wording",
                    "weight": 0.8,
                    "personal_relevance": "user_centered",
                    "source_ids": [source_id],
                },
                state=state,
            )

        updated = memory.execute(
            tool="memory/fix/edge",
            arguments={
                "operation": "update",
                "edge_id": edge_id,
                "relation": "stronger current relation",
                "weight_delta": 0.2,
                "personal_relevance": "user_centered",
                "source_ids": [source_id],
            },
            state=state,
        )["edge"]
        assert updated["weight"] == pytest.approx(0.9)
        assert updated["personal_relevance"] == 1.0

        disconnected = memory.execute(
            tool="memory/fix/edge",
            arguments={"operation": "disconnect", "edge_id": edge_id, "source_ids": [source_id]},
            state=state,
        )
        assert disconnected["edge"]["weight"] == 0.0
        assert edge_id not in state.viewed_edges
    finally:
        sources.close()
        repo.close()


def test_agent_forces_query_recall_before_answer_and_uses_same_loop_sync_gate(tmp_path) -> None:
    path = tmp_path / "graph.db"
    repo = GraphRepository(path)
    sources = GraphSourceStore(path)
    try:
        memory = _memory(repo, sources, {"사용자": [1.0, 0.0], "past context": [1.0, 0.0]})
        model = FakeModel(
            [
                {"action": "tool", "tool": "memory/recall", "arguments": {"query": "past context"}},
                {
                    "action": "answer",
                    "outcome": "completed",
                    "content": "done",
                    "graph_synced": True,
                },
            ]
        )
        agent = AgentLifecycle(repository=repo, model=model, source_store=sources, core_extension=memory)

        result = agent.run(user_id="u", user_text="question", turn_id="t1")

        assert result["answer"] == "done"
        assert _has_answer(model.schemas[0]) is False
        assert _has_answer(model.schemas[1]) is True
        answer_variant = next(
            variant
            for variant in _schema_variants(model.schemas[1])
            if (variant.get("properties") or {}).get("action", {}).get("const") == "answer"
        )
        assert answer_variant["properties"]["graph_synced"] == {"const": True}
        assert len(model.schemas) == 2
    finally:
        sources.close()
        repo.close()
