from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from mai.agent import AgentLifecycle, FunctionWorkTool
from mai.graph import GraphDiscoveryService, GraphRecallService, GraphRepository, GraphSourceStore
from mai.model import ModelContractError


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


def _answer(content: str) -> dict:
    return {"action": "answer", "outcome": "completed", "content": content}


def _tool_names(schema: dict) -> set[str]:
    variants = schema.get("oneOf", [schema])
    names = set()
    for variant in variants:
        tool = (variant.get("properties") or {}).get("tool") or {}
        if "const" in tool:
            names.add(str(tool["const"]))
    return names


def _lifecycle(repo: GraphRepository, sources: GraphSourceStore, model: FakeModel, tools=None) -> AgentLifecycle:
    return AgentLifecycle(
        repository=repo,
        model=model,
        discovery=GraphDiscoveryService(repo),
        recall=GraphRecallService(repo, source_store=sources),
        work_tools=tools or [],
        source_store=sources,
    )


def test_plain_answer_has_no_post_answer_memory_model_round(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    sources = GraphSourceStore(tmp_path / "g.db")
    try:
        model = FakeModel([_answer("fixed")])

        result = _lifecycle(repo, sources, model).run(user_id="owner", user_text="hello", turn_id="t1")

        assert result["answer"] == "fixed"
        assert result["status"] == "completed"
        assert result["memory"]["status"] == "agent_managed"
        assert result["memory"]["mutation_count"] == 0
        assert len(model.schemas) == 1
    finally:
        sources.close()
        repo.close()


def test_generated_memory_is_recallable_in_later_agent_round(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    sources = GraphSourceStore(tmp_path / "g.db")
    try:
        model = FakeModel(
            [
                {"action": "tool", "tool": "memory/recall", "arguments": {"query": "Mai"}},
                {
                    "action": "tool",
                    "tool": "memory/generate/node",
                    "arguments": {"kind": "concept", "name": "Mai", "source_ids": [1]},
                },
                {"action": "tool", "tool": "memory/recall", "arguments": {"node_id": 2}},
                _answer("stored"),
            ]
        )

        result = _lifecycle(repo, sources, model).run(
            user_id="owner",
            user_text="나는 Mai 프로젝트를 진행하고 있어.",
            turn_id="t1",
        )

        assert len(model.schemas) == 4
        assert result["memory"]["mutation_count"] == 1
        assert result["memory"]["new_node_count"] == 1
        recalled = result["work_events"][2]["result"]
        assert any(node["name"] == "Mai" for node in recalled["nodes"])

        matches = repo.lookup_nodes(user_id="owner", queries=["Mai"])["matches"]
        assert [node["name"] for node in matches] == ["Mai"]
    finally:
        sources.close()
        repo.close()


def test_generate_node_requires_prior_query_recall(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    sources = GraphSourceStore(tmp_path / "g.db")
    try:
        lifecycle = _lifecycle(repo, sources, FakeModel([_answer("unused")]))
        assert lifecycle.memory is not None
        state = lifecycle.memory.begin_turn(
            user_id="owner",
            turn_id="t1",
            user_text="new concept",
        )

        with pytest.raises(ModelContractError, match="requires a query recall first"):
            lifecycle.memory.generate_node(
                arguments={"kind": "concept", "name": "new concept", "source_ids": [1]},
                state=state,
            )
    finally:
        sources.close()
        repo.close()


def test_external_tool_can_be_directly_activated_by_exact_route(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    sources = GraphSourceStore(tmp_path / "g.db")
    calls = []
    try:
        def handler(arguments, context):
            calls.append(arguments["value"])
            return {"value": arguments["value"] * 2}

        tool = FunctionWorkTool(
            name="double",
            description="Double a number",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["value"],
                "properties": {"value": {"type": "integer"}},
            },
            handler=handler,
        )
        model = FakeModel(
            [
                {
                    "action": "tool",
                    "tool": "tool_route",
                    "arguments": {"path": "/file/extension/double/use"},
                },
                {"action": "tool", "tool": "double", "arguments": {"value": 4}},
                _answer("8"),
            ]
        )

        result = _lifecycle(repo, sources, model, [tool]).run(
            user_id="owner",
            user_text="double",
            turn_id="t1",
        )

        assert calls == [4]
        assert result["answer"] == "8"
        assert result["work_events"][0]["result"]["status"] == "activated"
        assert "double" not in _tool_names(model.schemas[0])
        assert "double" in _tool_names(model.schemas[1])
    finally:
        sources.close()
        repo.close()


def test_agent_loop_still_has_no_arbitrary_round_cap(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    sources = GraphSourceStore(tmp_path / "g.db")
    calls = []
    try:
        def handler(arguments, context):
            calls.append(arguments["n"])
            return {"n": arguments["n"]}

        tool = FunctionWorkTool(
            name="echo_number",
            description="Echo a number",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["n"],
                "properties": {"n": {"type": "integer"}},
            },
            handler=handler,
        )
        count = 25
        actions = [
            {
                "action": "tool",
                "tool": "tool_route",
                "arguments": {"path": "/file/extension/echo_number/use"},
            },
            *[
                {"action": "tool", "tool": "echo_number", "arguments": {"n": n}}
                for n in range(count)
            ],
            _answer("done"),
        ]
        model = FakeModel(actions)

        result = _lifecycle(repo, sources, model, [tool]).run(
            user_id="owner",
            user_text="loop",
            turn_id="t1",
        )

        assert calls == list(range(count))
        assert result["answer"] == "done"
        assert len(model.schemas) == count + 2
    finally:
        sources.close()
        repo.close()
