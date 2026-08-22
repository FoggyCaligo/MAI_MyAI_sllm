from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from mai.agent import AgentLifecycle, FunctionWorkTool
from mai.graph import GraphRepository, GraphSourceStore, SourceRecord
from mai.memory_agent_adapter import MemoryAgentAdapter
from mai.model import ModelContractError
from mai.versioned_memory_extension import VersionedAgentGraphMemoryExtension


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
    return VersionedAgentGraphMemoryExtension(
        repository=repo,
        source_store=sources,
        embedding=FakeEmbedding(vectors),
        embedding_model_name="fake-embed",
    )


def _schema_variants(schema: dict) -> list[dict]:
    return list(schema.get("oneOf", [schema]))


def _tool_names(schema: dict) -> set[str]:
    names: set[str] = set()
    for variant in _schema_variants(schema):
        tool = (variant.get("properties") or {}).get("tool") or {}
        if "const" in tool:
            names.add(str(tool["const"]))
    return names


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
        result = memory.execute(tool="memory/recall", arguments={"query": "Mai query"}, state=state)

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
            turn_id="seed",
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
        memory.execute(tool="memory/recall", arguments={"node_id": first["candidates"][0]["node_id"]}, state=state)
        second = memory.execute(tool="memory/recall", arguments={"query": "find C"}, state=state)
        opened = memory.execute(tool="memory/recall", arguments={"node_id": second["candidates"][0]["node_id"]}, state=state)

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
        memory = _memory(repo, sources, {"사용자": [1.0, 0.0], "first lookup": [0.0, 1.0], "first node": [0.0, 1.0]})
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


def test_edge_fix_appends_versions_and_disconnects_with_weight_zero(tmp_path) -> None:
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
        first_version = created["edge"]["version_id"]

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
        assert updated["version_id"] != first_version
        assert updated["weight"] == pytest.approx(0.9)
        assert updated["personal_relevance"] == 1.0

        disconnected = memory.execute(
            tool="memory/fix/edge",
            arguments={"operation": "disconnect", "edge_id": edge_id, "source_ids": [source_id]},
            state=state,
        )["edge"]
        assert disconnected["weight"] == 0.0
        assert disconnected["version_id"] != updated["version_id"]
        assert edge_id not in state.viewed_edges
        versions = repo.edge_versions(user_id="u", edge_id=edge_id)
        assert len(versions) == 3
        assert versions[0]["weight"] == 0.0
    finally:
        sources.close()
        repo.close()


def test_same_turn_rewrite_cannot_be_used_as_past_memory_evidence(tmp_path) -> None:
    path = tmp_path / "graph.db"
    repo = GraphRepository(path)
    sources = GraphSourceStore(path)
    try:
        a = repo.create_node(user_id="u", name="사용자")
        b = repo.create_node(user_id="u", name="직업")
        repo.set_node_embedding(user_id="u", node_id=a["node_id"], model="fake-embed", vector=[1.0, 0.0])
        repo.set_node_embedding(user_id="u", node_id=b["node_id"], model="fake-embed", vector=[0.9, 0.1])
        edge = repo.create_edge(
            user_id="u",
            start_node_id=a["node_id"],
            end_node_id=b["node_id"],
            relation="개발자",
            weight=0.8,
            personal_relevance=1.0,
            turn_id="old-turn",
        )
        old_source = sources.ensure_sources(
            user_id="u",
            turn_id="old-turn",
            records=[SourceRecord("user_message", "user", "나는 개발자야", {})],
        )[0]
        sources.link_sources(
            user_id="u",
            turn_id="old-turn",
            source_ids=[old_source],
            edge_version_id=edge["current_version_id"],
        )

        memory = _memory(repo, sources, {"사용자": [1.0, 0.0], "직업": [0.9, 0.1], "직업 기억": [1.0, 0.0]})
        state = memory.begin_turn(user_id="u", turn_id="current-turn", user_text="예전엔 디자이너였잖아")
        new_source = next(iter(state.available_source_ids))
        candidates = memory.execute(tool="memory/recall", arguments={"query": "직업 기억"}, state=state)["candidates"]
        for node_id in [a["node_id"], b["node_id"]]:
            assert node_id in {item["node_id"] for item in candidates}
            memory.execute(tool="memory/recall", arguments={"node_id": node_id}, state=state)

        memory.execute(
            tool="memory/fix/edge",
            arguments={
                "operation": "update",
                "edge_id": edge["edge_id"],
                "relation": "디자이너",
                "weight_delta": 0.0,
                "personal_relevance": "user_centered",
                "source_ids": [new_source],
            },
            state=state,
        )
        history = memory.execute(
            tool="memory/recall",
            arguments={"edge_id": edge["edge_id"]},
            state=state,
        )

        assert history["current_edge"]["relation"] == "디자이너"
        assert history["current_edge"]["version_turn_id"] == "current-turn"
        assert [item["relation"] for item in history["past_versions"]] == ["개발자"]
        assert history["past_versions"][0]["source_ids"] == [old_source]
        assert history["current_turn_versions_are_past_evidence"] is False
    finally:
        sources.close()
        repo.close()


def test_agent_first_round_exposes_only_vector_recall_then_opens_external_tools(tmp_path) -> None:
    path = tmp_path / "graph.db"
    repo = GraphRepository(path)
    sources = GraphSourceStore(path)
    try:
        memory = _memory(repo, sources, {"사용자": [1.0, 0.0], "past context": [1.0, 0.0]})
        adapter = MemoryAgentAdapter(memory)
        noop = FunctionWorkTool(
            name="noop",
            description="No-op external test tool",
            input_schema={"type": "object", "additionalProperties": False, "properties": {}},
            handler=lambda arguments, context: {"ok": True},
        )
        model = FakeModel(
            [
                {"action": "tool", "tool": "memory/recall", "arguments": {"query": "past context"}},
                {"action": "answer", "outcome": "completed", "content": "done", "graph_synced": True},
            ]
        )
        agent = AgentLifecycle(
            repository=repo,
            model=model,
            source_store=sources,
            core_extension=adapter,
            work_tools=[noop],
        )

        result = agent.run(user_id="u", user_text="question", turn_id="t1")

        assert result["answer"] == "done"
        assert _has_answer(model.schemas[0]) is False
        assert _tool_names(model.schemas[0]) == {"memory/recall"}
        first_arguments = model.schemas[0]["properties"]["arguments"]
        assert first_arguments["required"] == ["query"]
        assert _has_answer(model.schemas[1]) is True
        assert "tool_route" in _tool_names(model.schemas[1])
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


def test_agent_rejects_final_answer_without_graph_sync_acknowledgement(tmp_path) -> None:
    path = tmp_path / "graph.db"
    repo = GraphRepository(path)
    sources = GraphSourceStore(path)
    try:
        memory = _memory(repo, sources, {"사용자": [1.0, 0.0], "past context": [1.0, 0.0]})
        adapter = MemoryAgentAdapter(memory)
        model = FakeModel(
            [
                {"action": "tool", "tool": "memory/recall", "arguments": {"query": "past context"}},
                {"action": "answer", "outcome": "completed", "content": "done"},
            ]
        )
        agent = AgentLifecycle(repository=repo, model=model, source_store=sources, core_extension=adapter)

        with pytest.raises(ModelContractError, match="graph_synced=true"):
            agent.run(user_id="u", user_text="question", turn_id="t1")
    finally:
        sources.close()
        repo.close()
