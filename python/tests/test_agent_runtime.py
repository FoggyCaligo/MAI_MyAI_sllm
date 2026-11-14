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
        # Progress metadata can follow the tool result in the next request.
        # Assert the latest actual tool observation without assuming it is
        # the final message in the request.
        tool_messages = [
            message for message in adapter.requests[request_index].messages
            if message["role"] == "tool" and message.get("tool_name") == "terminal_run"
        ]
        assert tool_messages, "terminal failure must remain visible to the model"
        payload = json.loads(tool_messages[-1]["content"])
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
    assert "MAI tool result round=2 name=echo ok=true handler_started=true reused=false error_type=-" in caplog.text
    assert "MAI provisional answer verification round=3" in caplog.text
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
    assert "latest draft two" in joined  # Latest rejected draft is supplied only as correction context.
    assert not any(m.get("role") == "assistant" and m.get("content") == "latest draft two" for m in messages)
    correction_messages = [m for m in messages if m.get("role") == "system" and "rejected_provisional_answer" in m.get("content", "")]
    assert len(correction_messages) == 1
    assert "reasoning one" not in joined and "reasoning two" not in joined
    assert "grounding defect 1" not in joined
    assert "grounding defect 2" in joined
    assert '"correction_contract"' in joined
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


def test_tool_round_preserves_original_assistant_message_unchanged():
    registry = ToolRegistry()
    registry.add(name="echo", description="Echo", input_model=EchoInput,
                 handler=lambda text: {"echo": text})
    call = NativeToolCall("echo", {"text": "observed"}, index=4)
    original = assistant_turn(
        content="Original assistant explanation.",
        thinking="Original model thinking.",
        calls=(call,),
    )
    adapter = FakeAdapter([
        activate("echo"),
        original,
        assistant_turn(content="done"),
    ])
    result = run(AgentRuntime(adapter, registry).run_user_message("echo observed"))
    assert result.content == "done"
    history = adapter.requests[2].messages
    assert original.assistant_message in history
    assert {"role": "tool", "tool_name": "echo", "content": '{"echo":"observed"}'} in history


def test_tool_manual_returns_status_only_and_guidance_is_separate_system_context():
    registry = ToolRegistry()
    registry.add(name="echo", description="Echo", input_model=EchoInput,
                 handler=lambda text: text)
    registry.add_model_context(
        key="echo_runtime_guidance",
        context={"instructions": "SCOPED_TOOL_GUIDANCE"},
        tools=["echo"],
    )
    adapter = FakeAdapter([
        assistant_turn(
            thinking="THINKING_ON_ACTIVATION",
            calls=(NativeToolCall("tool_manual", {"tools": ["echo"]}),),
        ),
        assistant_turn(content="done"),
    ])
    result = run(AgentRuntime(adapter, registry).run_user_message("select echo"))
    assert result.content == "done"
    assert json.loads(result.tool_executions[0].content) == {
        "activated_tools": ["echo"], "available_next_round": True,
    }
    request = adapter.requests[1]
    assert any(m.get("role") == "system" and "SCOPED_TOOL_GUIDANCE" in m["content"]
               for m in request.messages)
    assert all("SCOPED_TOOL_GUIDANCE" not in m["content"]
               for m in request.messages if m.get("role") == "tool")
    assert any(m.get("role") == "assistant" and m.get("thinking") == "THINKING_ON_ACTIVATION"
               for m in request.messages)


def test_verifier_receives_tool_arguments_in_original_execution_order() -> None:
    registry = ToolRegistry()
    registry.add(name="echo", description="Echo.", input_model=EchoInput, handler=lambda text: text)

    class CapturingVerifier:
        async def verify(self, **kwargs):
            assert kwargs["tool_arguments"] == (
                {"tools": ["echo"]},
                {"text": "first"},
                {"text": "second"},
            )
            assert [item[0] for item in kwargs["tool_results"]] == [
                "tool_manual", "echo", "echo",
            ]
            from mai.agent.verification import _review_payload
            payload = _review_payload(
                candidate=kwargs["candidate"],
                messages=kwargs["messages"],
                tool_results=kwargs["tool_results"],
                tool_arguments=kwargs["tool_arguments"],
            )
            evidence = payload["tool_results_in_execution_order"]
            assert [item["arguments"] for item in evidence] == [
                {"tools": ["echo"]}, {"text": "first"}, {"text": "second"},
            ]
            assert [item["index"] for item in evidence] == [0, 1, 2]
            from mai.agent.verification import FinalVerificationResult
            return FinalVerificationResult(ok=True)

    adapter = FakeAdapter([
        activate("echo"),
        assistant_turn(calls=(NativeToolCall(name="echo", arguments={"text": "first"}),)),
        assistant_turn(calls=(NativeToolCall(name="echo", arguments={"text": "second"}),)),
        assistant_turn(content="done"),
    ])
    result = run(AgentRuntime(adapter, registry, final_verifier=CapturingVerifier()).run_user_message("Check echo"))
    assert result.content == "done"


def test_reasoning_loop_recovers_to_next_model_turn_without_partial_replay():
    from mai.llm.errors import ReasoningLoopDetected

    class RepeatingAdapter(FakeAdapter):
        async def chat(self, request):
            self.requests.append(deepcopy(request))
            if len(self.requests) <= 3:
                raise ReasoningLoopDetected({
                    "status": "repetition_suspected",
                    "thinking_chars": 6000,
                    "repeat_checks": 2,
                    "recent_similarity": 0.98,
                    "content_chars": 0,
                    "tool_fragment_count": 0,
                    "action": "interrupt_and_retry",
                })
            return assistant_turn(content="Recovered final answer")

    adapter = RepeatingAdapter([])
    progress = []
    timings = []
    result = run(AgentRuntime(adapter, ToolRegistry()).run_user_message(
        "check", on_model_stream_progress=lambda n, p: progress.append((n, p)),
        on_round_timing=lambda n, p: timings.append((n, p)),
    ))
    assert result.content == "Recovered final answer"
    assert result.model_rounds == 4
    assert len(adapter.requests) == 4
    for index in range(1, 4):
        messages = adapter.requests[index].messages
        assert sum(
            m.get("role") == "system" and "[REASONING_LOOP_DETECTED]" in m.get("content", "")
            for m in messages
        ) == 1
        assert "6000" not in str(messages)  # diagnostic data must not become model context
        assert not any(m.get("role") == "assistant" and m.get("thinking") for m in messages)
    assert [n for n, p in progress if p.get("recovery_event")] == [1, 2, 3]
    assert len([p for _, p in timings if p["status"] == "reasoning_loop_recovered"]) == 3


def test_recovery_preserves_completed_tool_outcome_without_reexecuting():
    from mai.llm.errors import ReasoningLoopDetected

    class InterruptedAdapter(FakeAdapter):
        async def chat(self, request):
            self.requests.append(deepcopy(request))
            if len(self.requests) == 3:
                raise ReasoningLoopDetected({
                    "status": "repetition_suspected", "thinking_chars": 2400,
                    "repeat_checks": 2, "recent_similarity": 0.99,
                })
            if not self.turns:
                raise AssertionError("unexpected model round")
            return self.turns.pop(0)

    calls = []
    registry = ToolRegistry()
    registry.add(
        name="echo", description="Echo text.", input_model=EchoInput,
        handler=lambda text: calls.append(text) or {"echo": text},
    )
    adapter = InterruptedAdapter([
        activate("echo"),
        assistant_turn(calls=(NativeToolCall(name="echo", arguments={"text": "once"}),)),
        assistant_turn(content="Completed"),
    ])
    result = run(AgentRuntime(adapter, registry).run_user_message("test"))
    assert result.content == "Completed"
    assert calls == ["once"]
    assert len([t for t in result.tool_executions if t.name == "echo"]) == 1
    messages = adapter.requests[-1].messages
    assert any(m.get("role") == "tool" and '"echo":"once"' in m.get("content", "") for m in messages)
    assert any("[REASONING_LOOP_DETECTED]" in m.get("content", "") for m in messages)
