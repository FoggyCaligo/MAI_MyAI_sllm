from __future__ import annotations

from dataclasses import dataclass, field

from mai.agent import AgentLifecycle, FunctionWorkTool


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


def _lifecycle(model: FakeModel, tools=None) -> AgentLifecycle:
    return AgentLifecycle(repository=None, model=model, work_tools=tools or [])


def test_plain_agent_answer_has_one_model_round() -> None:
    model = FakeModel([_answer("fixed")])

    result = _lifecycle(model).run(user_id="owner", user_text="hello", turn_id="t1")

    assert result["answer"] == "fixed"
    assert result["status"] == "completed"
    assert "memory" not in result
    assert len(model.schemas) == 1


def test_external_tool_can_be_directly_activated_by_exact_route() -> None:
    calls = []

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

    result = _lifecycle(model, [tool]).run(user_id="owner", user_text="double", turn_id="t1")

    assert calls == [4]
    assert result["answer"] == "8"
    assert result["work_events"][0]["result"]["status"] == "activated"
    assert "double" not in _tool_names(model.schemas[0])
    assert "double" in _tool_names(model.schemas[1])


def test_framework_tool_result_uses_user_transport_not_native_tool_role() -> None:
    tool = FunctionWorkTool(
        name="echo",
        description="Echo text",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["value"],
            "properties": {"value": {"type": "string"}},
        },
        handler=lambda arguments, context: {"value": arguments["value"]},
    )
    model = FakeModel(
        [
            {"action": "tool", "tool": "tool_route", "arguments": {"path": "/file/extension/echo/use"}},
            {"action": "tool", "tool": "echo", "arguments": {"value": "x"}},
            _answer("done"),
        ]
    )

    _lifecycle(model, [tool]).run(user_id="owner", user_text="echo", turn_id="t1")

    final_messages = model.messages[-1]
    assert all(message["role"] != "tool" for message in final_messages)
    assert any(
        message["role"] == "user" and message["content"].startswith("Framework tool result:")
        for message in final_messages
    )


def test_agent_loop_has_no_arbitrary_round_cap() -> None:
    calls = []

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
        {"action": "tool", "tool": "tool_route", "arguments": {"path": "/file/extension/echo_number/use"}},
        *[{"action": "tool", "tool": "echo_number", "arguments": {"n": n}} for n in range(count)],
        _answer("done"),
    ]
    model = FakeModel(actions)

    result = _lifecycle(model, [tool]).run(user_id="owner", user_text="loop", turn_id="t1")

    assert calls == list(range(count))
    assert result["answer"] == "done"
    assert len(model.schemas) == count + 2
