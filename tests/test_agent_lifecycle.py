from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from mai.agent import AgentLifecycle, FunctionWorkTool
from mai.final_memory import FinalMemoryExecutor
from mai.graph import GraphDiscoveryService, GraphRecallService, GraphRepository
from mai.memory_revise import ReviseMemoryTool
from mai.memory_write import WriteMemoryTool
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


def _answer(content: str) -> dict:
    return {"action": "answer", "outcome": "completed", "content": content}


def _memory_write(
    name: str,
    *,
    relation: str = "turn_memory",
    object_endpoint: dict | None = None,
    continue_memory: bool = False,
) -> dict:
    return {
        "action": "tool",
        "tool": "write_memory",
        "arguments": {
            "subject": {"kind": "user"},
            "relation": relation,
            "object": object_endpoint or {"new_node": {"name": name}},
        },
        "continue_memory": continue_memory,
    }


def _manual(tool: str) -> dict:
    return {"action": "tool", "tool": "tool_manual", "arguments": {"tool": tool}}


def _node(repo: GraphRepository, name: str) -> dict:
    return repo.create_node(
        user_id="owner",
        name=name,
        turn_id="seed",
        source_role="user",
        source_text=name,
    )


def _edge(repo: GraphRepository, a: int, relation: str, b: int) -> dict:
    return repo.create_or_reinforce_edge(
        user_id="owner",
        subject_node_id=a,
        relation=relation,
        object_node_id=b,
        turn_id="seed",
        source_role="turn",
        source_text=relation,
    )


def _lifecycle(repo, model, tools=None, memory_executor=None):
    return AgentLifecycle(
        repository=repo,
        model=model,
        discovery=GraphDiscoveryService(repo),
        recall=GraphRecallService(repo),
        memory_executor=memory_executor
        or FinalMemoryExecutor(
            writer=WriteMemoryTool(repo),
            reviser=ReviseMemoryTool(repo),
        ),
        work_tools=tools or [],
    )


def _tool_names(schema: dict) -> set[str]:
    variants = schema.get("oneOf", [schema])
    names = set()
    for variant in variants:
        tool = (variant.get("properties") or {}).get("tool") or {}
        if "const" in tool:
            names.add(str(tool["const"]))
    return names


def test_plain_answer_uses_one_extra_memory_round_before_release(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    try:
        model = FakeModel([_answer("fixed"), _memory_write("fixed")])

        result = _lifecycle(repo, model).run(user_id="owner", user_text="hello", turn_id="t1")

        assert len(model.schemas) == 2
        assert result["answer"] == "fixed"
        assert result["status"] == "completed"
        assert result["discovery"] == {"status": "agent_driven"}
        assert result["memory"]["status"] == "done"
        assert result["memory"]["mutation_count"] == 1
    finally:
        repo.close()


def test_work_tool_requires_manual_then_result_returns_to_model(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    try:
        calls = []

        def handler(arguments, context):
            calls.append((arguments, context.turn_id))
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
        model = FakeModel([
            _manual("double"),
            {"action": "tool", "tool": "double", "arguments": {"value": 4}},
            _answer("8"),
            _memory_write("8"),
        ])

        result = _lifecycle(repo, model, [tool]).run(user_id="owner", user_text="double", turn_id="t1")

        assert len(model.schemas) == 4
        assert calls == [({"value": 4}, "t1")]
        assert [event["tool"] for event in result["work_events"]] == ["tool_manual", "double"]
        assert result["work_events"][1]["result"] == {"value": 8}
        assert "tool_manual" in _tool_names(model.schemas[0])
        assert "double" not in _tool_names(model.schemas[0])
        assert "double" in _tool_names(model.schemas[1])
    finally:
        repo.close()


def test_lookup_and_recall_are_available_inside_the_same_agent_loop(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    try:
        anchor = repo.ensure_user_anchor(user_id="owner", turn_id="seed", source_text="owner")
        a = _node(repo, "MAI")
        edge = _edge(repo, anchor["node_id"], "has", a["node_id"])
        model = FakeModel([
            {"action": "tool", "tool": "node_lookup", "arguments": {"queries": ["MAI"]}},
            {"action": "tool", "tool": "recall_memory", "arguments": {"focus_node_id": a["node_id"]}},
            _answer("remembered"),
            _memory_write("remembered", object_endpoint={"existing_node_id": a["node_id"]}),
        ])

        result = _lifecycle(repo, model).run(user_id="owner", user_text="MAI", turn_id="t1")

        assert len(model.schemas) == 4
        assert [event["tool"] for event in result["work_events"][:2]] == ["node_lookup", "recall_memory"]
        assert result["memory"]["mutation_count"] == 1
        assert result["memory"]["mutations"][0]["edge"]["object_node_id"] == a["node_id"]
        recalled = AgentLifecycle._aggregate_recall([result["work_events"][1]["result"]])
        assert recalled is not None
        assert edge["edge_id"] in {item["edge_id"] for item in recalled["edges"]}
    finally:
        repo.close()


def test_recall_cannot_use_unlooked_up_id(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    try:
        a = _node(repo, "secret")
        model = FakeModel([
            {"action": "tool", "tool": "recall_memory", "arguments": {"focus_node_id": a["node_id"]}},
        ])

        with pytest.raises(ModelContractError):
            _lifecycle(repo, model).run(user_id="owner", user_text="x", turn_id="t1")
    finally:
        repo.close()


def test_fixed_answer_is_not_returned_when_memory_mutation_fails(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    try:
        model = FakeModel([
            _answer("must not release"),
            _memory_write("x", relation=""),
        ])

        with pytest.raises(ModelContractError, match="relation must be non-empty"):
            _lifecycle(repo, model).run(user_id="owner", user_text="hello", turn_id="t1")
    finally:
        repo.close()


def test_agent_loop_has_no_arbitrary_round_cap(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    try:
        count = 25
        calls = []

        def handler(arguments, context):
            calls.append(arguments["n"])
            return arguments["n"]

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
        actions = [_manual("echo_number")] + [
            {"action": "tool", "tool": "echo_number", "arguments": {"n": n}}
            for n in range(count)
        ] + [_answer("done"), _memory_write("done")]
        model = FakeModel(actions)

        result = _lifecycle(repo, model, [tool]).run(user_id="owner", user_text="loop", turn_id="t1")

        assert calls == list(range(count))
        assert result["answer"] == "done"
        assert len(model.schemas) == count + 3
    finally:
        repo.close()
