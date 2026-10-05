from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import logging

import pytest
from pydantic import BaseModel, ConfigDict

from mai.agent import AgentRunFailure, AgentRuntime, GuardConfig
from mai.llm.models import ModelTurn, NativeToolCall
from mai.tools import ToolRegistry


def run(coro):
    return asyncio.run(coro)


class EchoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str


class CommandInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: str


class FakeAdapter:
    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []

    async def chat(self, request):
        self.requests.append(deepcopy(request))
        if not self.turns:
            raise AssertionError("unexpected extra model round")
        return self.turns.pop(0)


def assistant_turn(*, content="", thinking="", calls=()):
    tool_calls = tuple(calls)
    message = {"role": "assistant", "content": content}
    if thinking:
        message["thinking"] = thinking
    if tool_calls:
        message["tool_calls"] = [
            {
                "type": "function",
                "function": {
                    **({"index": call.index} if call.index is not None else {}),
                    "name": call.name,
                    "arguments": dict(call.arguments),
                },
            }
            for call in tool_calls
        ]
    return ModelTurn(
        content=content,
        thinking=thinking,
        tool_calls=tool_calls,
        assistant_message=message,
    )


def _request_tool_names(request) -> list[str]:
    return [schema["function"]["name"] for schema in request.tools]


def test_runtime_completes_native_tool_round_trip() -> None:
    registry = ToolRegistry()
    registry.add(name="echo", description="Echo text.", input_model=EchoInput, handler=lambda text: {"echo": text})
    adapter = FakeAdapter([
        assistant_turn(thinking="Use echo.", calls=(NativeToolCall(name="echo", arguments={"text": "hello"}, index=0),)),
        assistant_turn(content="The tool returned hello.", thinking="Done."),
    ])
    result = run(AgentRuntime(adapter, registry).run_user_message("echo hello"))
    assert result.content == "The tool returned hello."
    assert result.model_rounds == 2
    assert result.tool_executions[0].ok is True
    assert result.tool_executions[0].handler_started is True
    followup_messages = adapter.requests[1].messages
    assert followup_messages[2]["role"] == "assistant"
    assert followup_messages[2]["tool_calls"][0]["function"]["name"] == "echo"
    assert followup_messages[3] == {"role": "tool", "tool_name": "echo", "content": '{"echo":"hello"}'}


def test_runtime_preserves_multiple_tool_calls_in_order() -> None:
    registry = ToolRegistry()
    registry.add(name="echo", description="Echo text.", input_model=EchoInput, handler=lambda text: text.upper())
    adapter = FakeAdapter([
        assistant_turn(calls=(NativeToolCall(name="echo", arguments={"text": "a"}, index=0), NativeToolCall(name="echo", arguments={"text": "b"}, index=1))),
        assistant_turn(content="done"),
    ])
    result = run(AgentRuntime(adapter, registry).run_user_message("two calls"))
    assert [execution.content for execution in result.tool_executions] == ["A", "B"]


def test_tool_failure_is_returned_as_visible_structured_tool_result() -> None:
    registry = ToolRegistry()
    def denied(text: str):
        raise PermissionError("denied")
    registry.add(name="denied", description="A tool that fails.", input_model=EchoInput, handler=denied)
    adapter = FakeAdapter([
        assistant_turn(calls=(NativeToolCall(name="denied", arguments={"text": "x"}),)),
        assistant_turn(content="I could not complete it."),
    ])
    result = run(AgentRuntime(adapter, registry).run_user_message("try"))
    payload = json.loads(result.tool_executions[0].content)
    assert payload == {"ok": False, "error_type": "PermissionError", "message": "denied"}
    assert result.tool_executions[0].handler_started is True


def test_model_can_continue_beyond_thirty_rounds_when_each_round_makes_progress() -> None:
    registry = ToolRegistry()
    registry.add(name="echo", description="Echo text.", input_model=EchoInput, handler=lambda text: text)
    turns = [
        assistant_turn(calls=(NativeToolCall(name="echo", arguments={"text": f"step-{index}"}),))
        for index in range(1, 33)
    ]
    turns.append(assistant_turn(content="completed long task"))
    adapter = FakeAdapter(turns)

    result = run(AgentRuntime(adapter, registry).run_user_message("long task"))

    assert result.content == "completed long task"
    assert result.model_rounds == 33
    assert len(result.tool_executions) == 32


def test_model_can_correct_terminal_command_after_five_distinct_failures() -> None:
    registry = ToolRegistry()

    def terminal(command: str):
        if command != "working-command":
            raise RuntimeError(f"failed command: {command}")
        return {"stdout": "done", "returncode": 0}

    registry.add(
        name="terminal_run",
        description="Test terminal.",
        input_model=CommandInput,
        handler=terminal,
    )
    failed_commands = [f"attempt-{index}" for index in range(1, 6)]
    adapter = FakeAdapter([
        *[
            assistant_turn(calls=(NativeToolCall(name="terminal_run", arguments={"command": command}),))
            for command in failed_commands
        ],
        assistant_turn(calls=(NativeToolCall(name="terminal_run", arguments={"command": "working-command"}),)),
        assistant_turn(content="completed after correcting the command"),
    ])

    result = run(AgentRuntime(adapter, registry).run_user_message("run the command"))

    assert result.content == "completed after correcting the command"
    assert result.model_rounds == 7
    assert len(result.tool_executions) == 6
    assert [execution.ok for execution in result.tool_executions] == [False, False, False, False, False, True]
    assert all(execution.handler_started for execution in result.tool_executions)
    for request_index, failed_command in enumerate(failed_commands, start=1):
        tool_message = adapter.requests[request_index].messages[-1]
        assert tool_message["role"] == "tool"
        assert tool_message["tool_name"] == "terminal_run"
        payload = json.loads(tool_message["content"])
        assert payload["ok"] is False
        assert payload["error_type"] == "RuntimeError"
        assert failed_command in payload["message"]


def test_empty_final_response_gets_one_structural_retry() -> None:
    adapter = FakeAdapter([
        assistant_turn(content=""),
        assistant_turn(content="recovered answer"),
    ])

    result = run(AgentRuntime(adapter, ToolRegistry()).run_user_message("answer me"))

    assert result.content == "recovered answer"
    assert result.model_rounds == 2
    retry_message = adapter.requests[1].messages[-1]
    assert retry_message["role"] == "system"
    assert "empty response" in retry_message["content"]


def test_second_empty_final_response_fails_explicitly() -> None:
    adapter = FakeAdapter([
        assistant_turn(content=""),
        assistant_turn(content="   "),
    ])

    with pytest.raises(AgentRunFailure) as exc_info:
        run(AgentRuntime(adapter, ToolRegistry()).run_user_message("answer me"))

    failure = exc_info.value
    assert failure.error_type == "EmptyFinalResponseError"
    assert failure.context.model_rounds == 2


def test_agent_flow_is_logged_to_uvicorn_terminal(caplog) -> None:
    registry = ToolRegistry()
    registry.add(name="echo", description="Echo text.", input_model=EchoInput, handler=lambda text: {"echo": text})
    adapter = FakeAdapter([
        assistant_turn(calls=(NativeToolCall(name="echo", arguments={"text": "hello"}),)),
        assistant_turn(content="done"),
    ])
    caplog.set_level(logging.INFO, logger="uvicorn.error")

    result = run(AgentRuntime(adapter, registry).run_user_message("echo hello"))

    assert result.content == "done"
    assert 'MAI tool call round=1 name=echo args={"text":"hello"}' in caplog.text
    assert "MAI tool result round=1 name=echo ok=true handler_started=true error_type=-" in caplog.text
    assert "MAI final candidate round=2" in caplog.text
    assert "MAI final accepted round=2" in caplog.text


def test_unknown_tool_is_not_substituted_with_another_tool() -> None:
    adapter = FakeAdapter([
        assistant_turn(calls=(NativeToolCall(name="missing", arguments={}),)),
        assistant_turn(content="missing tool"),
    ])
    result = run(AgentRuntime(adapter, ToolRegistry()).run_user_message("use missing"))
    assert result.tool_executions[0].error_type == "UnknownToolError"
    assert result.tool_executions[0].handler_started is False


def test_runtime_rejects_empty_user_message() -> None:
    runtime = AgentRuntime(FakeAdapter([]), ToolRegistry())
    with pytest.raises(ValueError, match="non-empty"):
        run(runtime.run_user_message("   "))


def test_rejected_draft_thinking_is_not_replayed_as_conversation() -> None:
    registry = ToolRegistry()
    registry.add(name="echo", description="Echo text.", input_model=EchoInput, handler=lambda text: text)
    draft = assistant_turn(content="unreleased draft")
    draft.assistant_message["thinking"] = "private mistaken reasoning"
    adapter = FakeAdapter([
        draft,
        assistant_turn(calls=(NativeToolCall(name="echo", arguments={"text": "evidence"}),)),
        assistant_turn(content="approved answer"),
    ])
    from mai.agent.verification import FinalVerificationResult, VerificationIssue

    class Verifier:
        async def verify(self, *, candidate, **kwargs):
            if candidate == "unreleased draft":
                return FinalVerificationResult(ok=False, issues=(VerificationIssue(
                    code="claim_grounding_failed", message="The draft lacks supporting evidence."),))
            return FinalVerificationResult(ok=True)

    result = run(AgentRuntime(adapter, registry, final_verifier=Verifier()).run_user_message("inspect"))
    retry = adapter.requests[1].messages
    assert all(m.get("role") != "assistant" or m.get("content") != "unreleased draft" for m in retry)
    assert "private mistaken reasoning" not in str(retry)
    assert '"delivered": false' in str(retry)
    assert "unreleased draft" not in str(retry)
    assert "unreleased draft" not in str(result.messages)
    assert any(m.get("content") == "approved answer" for m in result.messages)


def test_answer_without_tool_calls_has_no_preflight_or_execution_gate() -> None:
    registry = ToolRegistry()
    registry.add(name="echo", description="Echo text.", input_model=EchoInput, handler=lambda text: text)
    adapter = FakeAdapter([assistant_turn(content="The supplied context answers the request.")])
    result = run(AgentRuntime(adapter, registry).run_user_message("Use the context."))
    assert len(adapter.requests) == 1
    assert _request_tool_names(adapter.requests[0]) == ["echo"]
    assert result.tool_executions == ()
