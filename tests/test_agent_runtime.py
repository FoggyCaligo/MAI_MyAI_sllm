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


def activate(*names: str):
    return assistant_turn(calls=(NativeToolCall(name="tool_manual", arguments={"tools": list(names)}),))


def _request_tool_names(request) -> list[str]:
    return [schema["function"]["name"] for schema in request.tools]


def test_runtime_completes_native_tool_round_trip() -> None:
    registry = ToolRegistry()
    registry.add(name="echo", description="Echo text.", input_model=EchoInput, handler=lambda text: {"echo": text})
    adapter = FakeAdapter([
        activate("echo"),
        assistant_turn(thinking="Use echo.", calls=(NativeToolCall(name="echo", arguments={"text": "hello"}, index=0),)),
        assistant_turn(content="The tool returned hello.", thinking="Done."),
    ])
    result = run(AgentRuntime(adapter, registry).run_user_message("echo hello"))
    assert result.content == "The tool returned hello."
    assert result.model_rounds == 3
    assert result.tool_executions[1].ok is True
    assert result.tool_executions[1].handler_started is True
    followup_messages = adapter.requests[2].messages
    assert any(m.get("tool_calls") and m["tool_calls"][0]["function"]["name"] == "echo" for m in followup_messages)
    assert {"role": "tool", "tool_name": "echo", "content": '{"echo":"hello"}'} in followup_messages


def test_runtime_preserves_multiple_tool_calls_in_order() -> None:
    registry = ToolRegistry()
    registry.add(name="echo", description="Echo text.", input_model=EchoInput, handler=lambda text: text.upper())
    adapter = FakeAdapter([
        activate("echo"),
        assistant_turn(calls=(NativeToolCall(name="echo", arguments={"text": "a"}, index=0), NativeToolCall(name="echo", arguments={"text": "b"}, index=1))),
        assistant_turn(content="done"),
    ])
    result = run(AgentRuntime(adapter, registry).run_user_message("two calls"))
    assert [execution.content for execution in result.tool_executions if execution.name == "echo"] == ["A", "B"]


def test_tool_failure_is_returned_as_visible_structured_tool_result() -> None:
    registry = ToolRegistry()
    def denied(text: str):
        raise PermissionError("denied")
    registry.add(name="denied", description="A tool that fails.", input_model=EchoInput, handler=denied)
    adapter = FakeAdapter([
        activate("denied"),
        assistant_turn(calls=(NativeToolCall(name="denied", arguments={"text": "x"}),)),
        assistant_turn(content="I could not complete it."),
    ])
    result = run(AgentRuntime(adapter, registry).run_user_message("try"))
    payload = json.loads(result.tool_executions[1].content)
    assert payload == {"ok": False, "error_type": "PermissionError", "message": "denied"}
    assert result.tool_executions[1].handler_started is True


def test_model_can_continue_beyond_thirty_rounds_when_each_round_makes_progress() -> None:
    registry = ToolRegistry()
    registry.add(name="echo", description="Echo text.", input_model=EchoInput, handler=lambda text: text)
    turns = [activate("echo"), *[
        assistant_turn(calls=(NativeToolCall(name="echo", arguments={"text": f"step-{index}"}),))
        for index in range(1, 33)
    ]]
    turns.append(assistant_turn(content="completed long task"))
    adapter = FakeAdapter(turns)

    result = run(AgentRuntime(adapter, registry).run_user_message("long task"))

    assert result.content == "completed long task"
    assert result.model_rounds == 34
    assert len([item for item in result.tool_executions if item.name == "echo"]) == 32


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
        activate("terminal_run"),
        *[
            assistant_turn(calls=(NativeToolCall(name="terminal_run", arguments={"command": command}),))
            for command in failed_commands
        ],
        assistant_turn(calls=(NativeToolCall(name="terminal_run", arguments={"command": "working-command"}),)),
        assistant_turn(content="completed after correcting the command"),
    ])

    result = run(AgentRuntime(adapter, registry).run_user_message("run the command"))

    assert result.content == "completed after correcting the command"
    assert result.model_rounds == 8
    assert len(result.tool_executions) == 7
    assert [execution.ok for execution in result.tool_executions if execution.name == "terminal_run"] == [False, False, False, False, False, True]
    assert all(execution.handler_started for execution in result.tool_executions)
    for request_index, failed_command in enumerate(failed_commands, start=2):
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
        activate("echo"),
        assistant_turn(calls=(NativeToolCall(name="echo", arguments={"text": "hello"}),)),
        assistant_turn(content="done"),
    ])
    caplog.set_level(logging.INFO, logger="uvicorn.error")

    result = run(AgentRuntime(adapter, registry).run_user_message("echo hello"))

    assert result.content == "done"
    assert 'MAI tool call round=2 name=echo args={"text":"hello"}' in caplog.text
    assert "MAI tool result round=2 name=echo ok=true handler_started=true error_type=-" in caplog.text
    assert "MAI final candidate round=3" in caplog.text
    assert "MAI final accepted round=3" in caplog.text


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


def test_rejected_draft_replay_keeps_chronology_and_only_latest_body() -> None:
    from mai.agent.loop import AgentLoop
    from mai.agent.verification import FinalVerificationResult, VerificationIssue

    class RejectTwice:
        def __init__(self):
            self.attempts = 0

        async def verify(self, **kwargs):
            self.attempts += 1
            if self.attempts <= 2:
                return FinalVerificationResult(
                    ok=False,
                    issues=(VerificationIssue(
                        code="claim_grounding_failed",
                        message=f"grounding defect {self.attempts}",
                    ),),
                )
            return FinalVerificationResult(ok=True)

    adapter = FakeAdapter([
        assistant_turn(content="obsolete draft one", thinking="reasoning one"),
        assistant_turn(content="latest draft two", thinking="reasoning two"),
        assistant_turn(content="accepted"),
    ])
    result = run(AgentLoop(
        adapter, ToolRegistry(), final_verifier=RejectTwice(),
    ).run([{"role": "user", "content": "check"}]))
    assert result.content == "accepted"
    messages = adapter.requests[2].messages
    joined = str(messages)
    assert "obsolete draft one" not in joined
    assert joined.count("latest draft two") == 1
    assert "reasoning one" not in joined and "reasoning two" not in joined
    assert "grounding defect 1" not in joined
    assert "grounding defect 2" in joined
    assert '"rejected_draft": "latest draft two"' in joined
    assert '"verifier_feedback"' in joined


def test_native_tool_call_arguments_are_not_duplicated_in_tool_result() -> None:
    registry = ToolRegistry()
    registry.add(name="echo", description="Echo.", input_model=EchoInput,
                 handler=lambda text: {"output": "ok"})
    adapter = FakeAdapter([
        activate("echo"),
        assistant_turn(thinking="check the tool", calls=(
            NativeToolCall(name="echo", arguments={"text": "unique-argument"}),)),
        assistant_turn(content="finished"),
    ])
    run(AgentRuntime(adapter, registry).run_user_message("test"))
    messages = adapter.requests[2].messages
    assistant_calls = [m for m in messages if m.get("tool_calls") and m["tool_calls"][0]["function"]["name"] == "echo"]
    tool_results = [m for m in messages if m.get("role") == "tool" and m.get("tool_name") == "echo"]
    assert len(assistant_calls) == 1
    assert len(tool_results) == 1
    assert assistant_calls[0]["tool_calls"][0]["function"]["arguments"] == {
        "text": "unique-argument",
    }
    assert "unique-argument" not in tool_results[0]["content"]
    assert "check the tool" in assistant_calls[0]["thinking"]
