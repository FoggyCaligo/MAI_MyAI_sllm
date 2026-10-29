from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel, ConfigDict

from mai.agent.loop import AgentLoop, AgentRunFailure
from mai.agent.task_state import (
    AnswerVerification, GoalCompletion, TaskLifecycle, TaskObservation, TaskState,
)
from mai.llm.models import ModelTurn, NativeToolCall
from mai.tools.registry import ToolRegistry


class FakeAdapter:
    def __init__(self, turns):
        self.turns = iter(turns)

    async def chat(self, request):
        return next(self.turns)


class EchoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str


def turn(content="", calls=()):
    return ModelTurn(
        content=content, thinking="", tool_calls=tuple(calls),
        assistant_message={"role": "assistant", "content": content},
    )


def run(coro):
    return asyncio.run(coro)


def test_state_rejects_unproven_completion_and_out_of_order_evidence():
    state = TaskState(goal="delete a remote branch")
    state.start()
    with pytest.raises(RuntimeError):
        state.complete()
    with pytest.raises(ValueError):
        state.observe(TaskObservation(1, "echo", True, None, None))
    state.observe(TaskObservation(0, "echo", True, None, "evidence"))
    state.verify_answer(accepted=True)
    state.complete()
    assert state.lifecycle is TaskLifecycle.COMPLETED
    assert state.goal_completion is GoalCompletion.UNVERIFIED


def test_simple_answer_always_has_task_state_without_tool_calls():
    result = run(AgentLoop(FakeAdapter([turn("hello")]), ToolRegistry()).run([
        {"role": "user", "content": "say hello"},
    ]))
    state = result.task_state
    assert state is not None
    assert state.goal == "say hello"
    assert state.lifecycle is TaskLifecycle.COMPLETED
    assert state.answer_verification is AnswerVerification.ACCEPTED
    assert state.goal_completion is GoalCompletion.UNVERIFIED
    assert state.observations == []


def test_tool_success_is_observed_but_goal_not_marked_verified():
    registry = ToolRegistry()
    registry.add(name="echo", description="Echo", input_model=EchoInput, handler=lambda text: text)
    activation = NativeToolCall(name="tool_manual", arguments={"tools": ["echo"]})
    call = NativeToolCall(name="echo", arguments={"text": "ok"})
    adapter = FakeAdapter([turn(calls=(activation,)), turn(calls=(call,)), turn("done")])
    result = run(AgentLoop(adapter, registry).run([{"role": "user", "content": "do something"}]))
    state = result.task_state
    assert len(state.observations) == 2
    assert all(obs.ok for obs in state.observations)
    assert state.goal_completion is GoalCompletion.UNVERIFIED


def test_failure_context_preserves_task_state_and_error():
    adapter = FakeAdapter([turn(""), turn("")])
    with pytest.raises(AgentRunFailure) as exc_info:
        run(AgentLoop(adapter, ToolRegistry()).run([{"role": "user", "content": "answer"}]))
    state = exc_info.value.context.task_state
    assert state.lifecycle is TaskLifecycle.FAILED
    assert state.failure_type == "EmptyFinalResponseError"
    assert state.answer_verification is AnswerVerification.NOT_RUN
