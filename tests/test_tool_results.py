from __future__ import annotations

import asyncio
import json
from copy import deepcopy

import pytest
from pydantic import BaseModel, ConfigDict

from mai.agent import AgentRuntime
from mai.agent.tool_results import (
    ToolResultNotFoundError,
    ToolResultReadLimitError,
    ToolResultStore,
    register_tool_result_tools,
)
from mai.llm.models import ModelTurn, NativeToolCall
from mai.tools import ToolRegistry


def run(coro):
    return asyncio.run(coro)


class EchoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str


class FakeAdapter:
    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []

    async def chat(self, request):
        self.requests.append(deepcopy(request))
        if not self.turns:
            raise AssertionError("unexpected extra model round")
        return self.turns.pop(0)


def assistant_turn(*, content="", calls=()):
    tool_calls = tuple(calls)
    message = {"role": "assistant", "content": content}
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
        thinking="",
        tool_calls=tool_calls,
        assistant_message=message,
    )


def split_page(text: str) -> tuple[dict[str, object], str]:
    metadata_text, separator, content = text.partition("\n")
    assert separator == "\n"
    return json.loads(metadata_text), content


def test_large_result_is_bounded_and_can_be_read_by_range() -> None:
    source = "0123456789" * 500
    store = ToolResultStore(max_inline_chars=1024)

    first_page = store.model_view(source)
    metadata, content = split_page(first_page)

    assert len(first_page) <= 1024
    assert metadata["total_chars"] == len(source)
    assert metadata["offset"] == 0
    assert metadata["returned_chars"] == len(content)
    assert metadata["complete"] is False
    assert content == source[: len(content)]

    next_offset = int(metadata["next_offset"])
    second_page = store.read(
        result_id=str(metadata["result_id"]),
        offset=next_offset,
        limit=100,
    )
    second_metadata, second_content = split_page(second_page)
    assert len(second_page) <= 1024
    assert second_metadata["offset"] == next_offset
    assert second_content == source[next_offset : next_offset + len(second_content)]


def test_large_result_has_structural_compact_history_reference() -> None:
    source = "x" * 5000
    store = ToolResultStore(max_inline_chars=1024)

    views = store.model_views(source)
    assert len(views.initial_content) <= 1024
    assert views.compact_history_content is not None

    first_metadata, _ = split_page(views.initial_content)
    compact = json.loads(views.compact_history_content)
    assert compact == {
        "result_id": first_metadata["result_id"],
        "total_chars": len(source),
        "content_compacted": True,
        "read_with": "tool_result_read",
        "max_read_chars": store.max_read_chars,
    }


def test_tool_result_read_rejects_unknown_id_and_oversized_page() -> None:
    store = ToolResultStore(max_inline_chars=1024)
    with pytest.raises(ToolResultReadLimitError):
        store.read(result_id="missing", offset=0, limit=store.max_read_chars + 1)
    with pytest.raises(ToolResultNotFoundError):
        store.read(result_id="missing", offset=0, limit=1)


def test_agent_observer_and_next_round_receive_same_bounded_scope() -> None:
    source = "x" * 5000
    registry = ToolRegistry()
    registry.add(
        name="echo",
        description="Return supplied text.",
        input_model=EchoInput,
        handler=lambda text: text,
    )
    store = ToolResultStore(max_inline_chars=1024)
    register_tool_result_tools(registry, store)
    adapter = FakeAdapter([
        assistant_turn(calls=(NativeToolCall(name="echo", arguments={"text": source}),)),
        assistant_turn(content="done"),
    ])
    observed = []

    result = run(AgentRuntime(
        adapter,
        registry,
        tool_result_store=store,
    ).run_user_message("return a large result", on_tool_execution=observed.append))

    assert result.content == "done"
    assert len(observed) == 1
    assert len(observed[0].content) <= 1024
    assert observed[0].content != source
    tool_message = adapter.requests[1].messages[-1]
    assert tool_message["role"] == "tool"
    assert tool_message["content"] == observed[0].content
    metadata, visible_content = split_page(observed[0].content)
    assert metadata["total_chars"] == len(source)
    assert visible_content == source[: len(visible_content)]

    stored_tool_message = next(message for message in result.messages if message.get("role") == "tool")
    compact = json.loads(stored_tool_message["content"])
    assert compact["result_id"] == metadata["result_id"]
    assert compact["content_compacted"] is True


def test_large_tool_page_is_compacted_before_later_model_rounds() -> None:
    source = "z" * 5000
    registry = ToolRegistry()
    registry.add(
        name="echo",
        description="Return supplied text.",
        input_model=EchoInput,
        handler=lambda text: text,
    )
    store = ToolResultStore(max_inline_chars=1024)
    register_tool_result_tools(registry, store)
    adapter = FakeAdapter([
        assistant_turn(calls=(NativeToolCall(name="echo", arguments={"text": source}),)),
        assistant_turn(calls=(NativeToolCall(name="echo", arguments={"text": "small"}),)),
        assistant_turn(content="done"),
    ])

    result = run(AgentRuntime(
        adapter,
        registry,
        tool_result_store=store,
    ).run_user_message("use two tool rounds"))

    assert result.content == "done"
    first_round_tool = [message for message in adapter.requests[1].messages if message.get("role") == "tool"][0]
    first_metadata, _ = split_page(first_round_tool["content"])

    third_request_tools = [message for message in adapter.requests[2].messages if message.get("role") == "tool"]
    assert len(third_request_tools) == 2
    compact = json.loads(third_request_tools[0]["content"])
    assert compact["result_id"] == first_metadata["result_id"]
    assert compact["content_compacted"] is True
    assert third_request_tools[1]["content"] == "small"
