from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from mai.agent import AgentLifecycle, FunctionWorkTool
from mai.graph import GraphRepository, GraphSourceStore, SourceRecord
from mai.memory_agent_adapter import MemoryAgentAdapter
from mai.model import ModelContractError
from mai.working_graph_memory import WorkingGraphMemoryExtension


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

    def structured(self, *, messages, schema):
        self.schemas.append(schema)
        if not self.actions:
            raise AssertionError("unexpected model round")
        return self.actions.pop(0)


def _memory(repo, sources, vectors):
    return WorkingGraphMemoryExtension(
        repository=repo,
        source_store=sources,
        embedding=FakeEmbedding(vectors),
        embedding_model_name="fake-embed",
    )


def _variants(schema: dict) -> list[dict]:
    return list(schema.get("oneOf", [schema]))


def _tool_names(schema: dict) -> set[str]:
    result = set()
    for variant in _variants(schema):
        tool = ((variant.get("properties") or {}).get("tool") or {}).get("const")
        if tool:
            result.add(str(tool))
    return result


def _has_answer(schema: dict) -> bool:
    return any(
        ((variant.get("properties") or {}).get("action") or {}).get("const") == "answer"
        for variant in _variants(schema)
    )


def _seed_pair(repo):
    a = repo.create_node(user_id="u", name="A")
    b = repo.create_node(user_id="u", name="B")
    repo.set_node_embedding(user_id="u", node_id=a["node_id"], model="fake-embed", vector=[1.0, 0.0])
    repo.set_node_embedding(user_id="u", node_id=b["node_id"], model="fake-embed", vector=[0.9, 0.1])
    return a, b


def _open_pair(memory, state, a, b):
    memory.execute(tool="memory/recall", arguments={"query": "pair"}, state=state)
    memory.execute(tool="memory/recall", arguments={"node_id": a["node_id"]}, state=state)
    memory.execute(tool="memory/recall", arguments={"node_id": b["node_id"]}, state=state)


def test_query_candidates_do_not_open_working_graph(tmp_path) -> None:
    path = tmp_path / "graph.db"
    repo = GraphRepository(path)
    sources = GraphSourceStore(path)
    memory = None
    try:
        node = repo.create_node(user_id="u", name="Mai")
        repo.set_node_embedding(user_id="u", node_id=node["node_id"], model="fake-embed", vector=[0.0, 1.0])
        memory = _memory(repo, sources, {"사용자": [1.0, 0.0], "Mai query": [0.0, 1.0]})
        state = memory.begin_turn(user_id="u", turn_id="t1", user_text="hello")
        result = memory.execute(tool="memory/recall", arguments={"query": "Mai query"}, state=state)
        assert result["candidates"][0]["node_id"] == node["node_id"]
        assert result["viewed_graph"] == {"nodes": [], "edges": []}
    finally:
        if memory is not None:
            memory.abort_turn(turn_id="t1")
        sources.close()
        repo.close()


def test_recall_accumulates_only_opened_one_hop_regions(tmp_path) -> None:
    path = tmp_path / "graph.db"
    repo = GraphRepository(path)
    sources = GraphSourceStore(path)
    memory = None
    try:
        a, b = _seed_pair(repo)
        c = repo.create_node(user_id="u", name="C")
        repo.set_node_embedding(user_id="u", node_id=c["node_id"], model="fake-embed", vector=[0.0, 1.0])
        repo.create_edge(
            user_id="u", start_node_id=a["node_id"], end_node_id=b["node_id"],
            relation="A to B", weight=1.0, personal_relevance=0.5, turn_id="seed",
        )
        memory = _memory(
            repo,
            sources,
            {"사용자": [0.2, 0.8], "find A": [1.0, 0.0], "find C": [0.0, 1.0]},
        )
        state = memory.begin_turn(user_id="u", turn_id="t1", user_text="hello")
        memory.execute(tool="memory/recall", arguments={"query": "find A"}, state=state)
        memory.execute(tool="memory/recall", arguments={"node_id": a["node_id"]}, state=state)
        assert {item["name"] for item in state.viewed_nodes.values()} == {"A", "B"}
        memory.execute(tool="memory/recall", arguments={"query": "find C"}, state=state)
        memory.execute(tool="memory/recall", arguments={"node_id": c["node_id"]}, state=state)
        assert {item["name"] for item in state.viewed_nodes.values()} == {"A", "B", "C"}
    finally:
        if memory is not None:
            memory.abort_turn(turn_id="t1")
        sources.close()
        repo.close()


def test_pending_node_does_not_exist_in_actual_graph_until_commit(tmp_path) -> None:
    path = tmp_path / "graph.db"
    repo = GraphRepository(path)
    sources = GraphSourceStore(path)
    try:
        memory = _memory(repo, sources, {"사용자": [1.0, 0.0], "lookup": [0.0, 1.0], "새 노드": [0.0, 1.0]})
        state = memory.begin_turn(user_id="u", turn_id="t1", user_text="source fact")
        source_id = next(iter(state.available_source_ids))
        memory.execute(tool="memory/recall", arguments={"query": "lookup"}, state=state)
        staged = memory.execute(
            tool="memory/generate/node",
            arguments={"kind": "concept", "name": "새 노드", "source_ids": [source_id]},
            state=state,
        )["node"]
        assert staged["node_id"] < 0 and staged["pending"] is True
        assert all(item["name"] != "새 노드" for item in repo.active_node_embeddings(user_id="u", model="fake-embed"))
        commit = memory.commit_turn(turn_id="t1")
        actual_id = commit["node_id_map"][staged["node_id"]]
        assert repo.get_node(user_id="u", node_id=actual_id)["name"] == "새 노드"
    finally:
        sources.close()
        repo.close()


def test_edge_history_separates_actual_from_working_until_commit(tmp_path) -> None:
    path = tmp_path / "graph.db"
    repo = GraphRepository(path)
    sources = GraphSourceStore(path)
    try:
        a, b = _seed_pair(repo)
        edge = repo.create_edge(
            user_id="u", start_node_id=a["node_id"], end_node_id=b["node_id"],
            relation="개발자", weight=0.8, personal_relevance=1.0, turn_id="old-turn",
        )
        old_source = sources.ensure_sources(
            user_id="u", turn_id="old-turn",
            records=[SourceRecord("user_message", "user", "나는 개발자야", {})],
        )[0]
        sources.link_sources(
            user_id="u", turn_id="old-turn", source_ids=[old_source], edge_version_id=edge["current_version_id"],
        )
        memory = _memory(repo, sources, {"사용자": [0.0, 1.0], "pair": [1.0, 0.0]})
        state = memory.begin_turn(user_id="u", turn_id="new-turn", user_text="예전엔 디자이너였잖아")
        new_source = next(iter(state.available_source_ids))
        _open_pair(memory, state, a, b)
        memory.execute(
            tool="memory/fix/edge",
            arguments={
                "operation": "update", "edge_id": edge["edge_id"], "relation": "디자이너",
                "weight_delta": 0.0, "personal_relevance": "user_centered", "source_ids": [new_source],
            },
            state=state,
        )
        assert repo.get_edge(user_id="u", edge_id=edge["edge_id"])["relation"] == "개발자"
        history = memory.execute(tool="memory/recall", arguments={"edge_id": edge["edge_id"]}, state=state)
        assert history["actual_current"]["relation"] == "개발자"
        assert history["working_current"]["relation"] == "디자이너"
        assert history["working_state_is_past_evidence"] is False
        memory.commit_turn(turn_id="new-turn")
        committed = repo.get_edge(user_id="u", edge_id=edge["edge_id"])
        assert committed["relation"] == "디자이너"
        assert committed["version_turn_id"] == "new-turn"
        assert len(repo.edge_versions(user_id="u", edge_id=edge["edge_id"])) == 2
    finally:
        sources.close()
        repo.close()


def test_disconnect_commits_weight_zero_without_deleting_logical_edge(tmp_path) -> None:
    path = tmp_path / "graph.db"
    repo = GraphRepository(path)
    sources = GraphSourceStore(path)
    try:
        a, b = _seed_pair(repo)
        edge = repo.create_edge(
            user_id="u", start_node_id=a["node_id"], end_node_id=b["node_id"],
            relation="connected", weight=0.7, personal_relevance=0.5, turn_id="seed",
        )
        memory = _memory(repo, sources, {"사용자": [0.0, 1.0], "pair": [1.0, 0.0]})
        state = memory.begin_turn(user_id="u", turn_id="disconnect-turn", user_text="disconnect")
        source_id = next(iter(state.available_source_ids))
        _open_pair(memory, state, a, b)
        memory.execute(
            tool="memory/fix/edge",
            arguments={"operation": "disconnect", "edge_id": edge["edge_id"], "source_ids": [source_id]},
            state=state,
        )
        assert repo.get_edge(user_id="u", edge_id=edge["edge_id"])["weight"] == pytest.approx(0.7)
        memory.commit_turn(turn_id="disconnect-turn")
        assert repo.get_edge(user_id="u", edge_id=edge["edge_id"])["weight"] == 0.0
        assert repo.one_hop_neighborhood(user_id="u", focus_node_id=a["node_id"])["edges"] == []
    finally:
        sources.close()
        repo.close()


def test_abort_discards_staged_semantic_changes(tmp_path) -> None:
    path = tmp_path / "graph.db"
    repo = GraphRepository(path)
    sources = GraphSourceStore(path)
    try:
        memory = _memory(repo, sources, {"사용자": [1.0, 0.0], "lookup": [0.0, 1.0]})
        state = memory.begin_turn(user_id="u", turn_id="t1", user_text="source fact")
        source_id = next(iter(state.available_source_ids))
        memory.execute(tool="memory/recall", arguments={"query": "lookup"}, state=state)
        memory.execute(
            tool="memory/generate/node",
            arguments={"kind": "concept", "name": "discard me", "source_ids": [source_id]},
            state=state,
        )
        memory.abort_turn(turn_id="t1")
        with pytest.raises(RuntimeError, match="missing"):
            memory.commit_turn(turn_id="t1")
    finally:
        sources.close()
        repo.close()


def test_agent_first_round_requires_recall_and_final_graph_sync(tmp_path) -> None:
    path = tmp_path / "graph.db"
    repo = GraphRepository(path)
    sources = GraphSourceStore(path)
    try:
        memory = _memory(repo, sources, {"사용자": [1.0, 0.0], "past": [1.0, 0.0]})
        adapter = MemoryAgentAdapter(memory)
        noop = FunctionWorkTool(
            name="noop", description="noop",
            input_schema={"type": "object", "additionalProperties": False, "properties": {}},
            handler=lambda arguments, context: {"ok": True},
        )
        model = FakeModel([
            {"action": "tool", "tool": "memory/recall", "arguments": {"query": "past"}},
            {"action": "answer", "outcome": "completed", "content": "done", "graph_synced": True},
        ])
        agent = AgentLifecycle(
            repository=repo, model=model, source_store=sources, core_extension=adapter, work_tools=[noop],
        )
        result = agent.run(user_id="u", user_text="question", turn_id="t1")
        assert result["answer"] == "done"
        assert _has_answer(model.schemas[0]) is False
        assert _tool_names(model.schemas[0]) == {"memory/recall"}
        assert _has_answer(model.schemas[1]) is True
        memory.abort_turn(turn_id="t1")
    finally:
        sources.close()
        repo.close()


def test_agent_rejects_answer_without_graph_sync(tmp_path) -> None:
    path = tmp_path / "graph.db"
    repo = GraphRepository(path)
    sources = GraphSourceStore(path)
    try:
        memory = _memory(repo, sources, {"사용자": [1.0, 0.0], "past": [1.0, 0.0]})
        adapter = MemoryAgentAdapter(memory)
        model = FakeModel([
            {"action": "tool", "tool": "memory/recall", "arguments": {"query": "past"}},
            {"action": "answer", "outcome": "completed", "content": "done"},
        ])
        agent = AgentLifecycle(repository=repo, model=model, source_store=sources, core_extension=adapter)
        with pytest.raises(ModelContractError, match="graph_synced=true"):
            agent.run(user_id="u", user_text="question", turn_id="t1")
        memory.abort_turn(turn_id="t1")
    finally:
        sources.close()
        repo.close()
