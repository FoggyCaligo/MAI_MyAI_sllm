from __future__ import annotations

import asyncio
from copy import deepcopy
import json

from pydantic import BaseModel, ConfigDict

from mai.agent.runtime import AgentRuntime
from mai.llm.models import ModelTurn, NativeToolCall
from mai.llm.ollama import OllamaAdapter
from mai.tools.registry import ToolRegistry


def run(coro):
    return asyncio.run(coro)


class EchoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str


class FakeOllamaAdapter(OllamaAdapter):
    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []

    async def chat(self, request):
        self.requests.append(deepcopy(request))
        if not self.turns:
            raise AssertionError("unexpected extra model turn")
        return self.turns.pop(0)


def final_turn(content: str) -> ModelTurn:
    return ModelTurn(
        content=content,
        thinking="",
        tool_calls=(),
        assistant_message={"role": "assistant", "content": content},
    )


def tool_turn(call: NativeToolCall) -> ModelTurn:
    return ModelTurn(
        content="",
        thinking="",
        tool_calls=(call,),
        assistant_message={
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "type": "function",
                "function": {"name": call.name, "arguments": dict(call.arguments)},
            }],
        },
    )


def test_ollama_runtime_runs_one_preflight_and_freezes_required_tools() -> None:
    call = NativeToolCall(name="echo", arguments={"text": "checked"})
    adapter = FakeOllamaAdapter([
        final_turn('{"required_tools":["echo"]}'),
        final_turn("premature answer"),
        tool_turn(call),
        final_turn("done"),
    ])
    executed = []
    registry = ToolRegistry()
    registry.add(
        name="echo",
        description="Echo text from the environment.",
        input_model=EchoInput,
        handler=lambda text: executed.append(text) or text,
    )

    result = run(AgentRuntime(adapter, registry).run_user_message("inspect it"))

    assert result.content == "done"
    assert executed == ["checked"]
    assert len(adapter.requests) == 4

    preflight = adapter.requests[0]
    assert preflight.tools == ()
    assert preflight.think is False
    assert preflight.response_format is not None
    assert preflight.stage == "preflight"

    first_agent_round = adapter.requests[1]
    assert len(first_agent_round.tools) == 1

    correction_round = adapter.requests[2]
    correction_messages = [
        message["content"]
        for message in correction_round.messages
        if message.get("role") == "system"
    ]
    assert any("missing required tools" in message for message in correction_messages)


def test_explicit_requirements_skip_preflight() -> None:
    from mai.agent.requirements import FrozenToolRequirements

    adapter = FakeOllamaAdapter([final_turn("done")])
    registry = ToolRegistry()

    result = run(AgentRuntime(adapter, registry).run_user_message(
        "answer",
        requirements=FrozenToolRequirements(frozenset()),
    ))

    assert result.content == "done"
    assert len(adapter.requests) == 1


def test_preflight_separates_assistant_context_from_factual_evidence() -> None:
    adapter = FakeOllamaAdapter([
        final_turn('{"required_tools":[]}'),
        final_turn("done"),
    ])

    result = run(AgentRuntime(adapter, ToolRegistry()).run_user_message(
        "그 제품에 대해 다시 답해줘.",
        prior_messages=(
            {"role": "user", "content": "Pilot 30ml 블랙을 사용해."},
            {"role": "assistant", "content": "그 잉크는 고점도입니다."},
        ),
    ))

    assert result.content == "done"
    preflight = adapter.requests[0]
    payload = json.loads(preflight.messages[1]["content"])
    assert payload["request_context"] == [
        {"role": "user", "content": "Pilot 30ml 블랙을 사용해."},
        {"role": "assistant", "content": "그 잉크는 고점도입니다."},
    ]
    assert payload["factual_evidence"] == [
        {
            "evidence_id": "prior-user-message-0",
            "source": "user",
            "content": "Pilot 30ml 블랙을 사용해.",
        },
        {
            "evidence_id": "current-user-message",
            "source": "user",
            "content": "그 제품에 대해 다시 답해줘.",
        },
    ]
    system_prompt = preflight.messages[0]["content"]
    assert "prior assistant message" in system_prompt
    assert "training knowledge is not evidence" in system_prompt
