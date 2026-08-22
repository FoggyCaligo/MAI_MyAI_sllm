from __future__ import annotations

import ast

from mai.context import compact_tool_event
from mai.model_context import prepare_model_messages, use_model_context


def test_recent_dialogue_and_date_are_injected_before_current_user() -> None:
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "current"},
    ]
    recent = [
        {"role": "user", "content": "previous question"},
        {"role": "assistant", "content": "previous answer"},
    ]

    with use_model_context(recent_messages=recent, recent_tool_operations=[]):
        prepared = prepare_model_messages(messages)

    assert "Current date:" in prepared[0]["content"]
    assert prepared[1] == {"role": "user", "content": "previous question"}
    assert prepared[2] == {"role": "assistant", "content": "previous answer"}
    assert prepared[3] == {"role": "user", "content": "current"}
    assert messages[0]["content"] == "system"


def test_recent_tool_operations_are_limited_to_five() -> None:
    operations = [
        {"tool": "file_search", "arguments": {"pattern": str(index)}, "result": {"count": index}}
        for index in range(7)
    ]
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "current"},
    ]

    with use_model_context(recent_messages=[], recent_tool_operations=operations):
        prepared = prepare_model_messages(messages)

    context_message = prepared[1]
    assert context_message["role"] == "system"
    assert "Recent tool operations" in context_message["content"]
    assert '"pattern": "0"' not in context_message["content"]
    assert '"pattern": "1"' not in context_message["content"]
    assert '"pattern": "2"' in context_message["content"]
    assert '"pattern": "6"' in context_message["content"]


def test_current_tool_message_is_compacted_without_mutating_original() -> None:
    content = "x" * 10000
    event = {
        "tool": "file_read",
        "arguments": {"path": "/tmp/a.txt"},
        "result": {"path": "/tmp/a.txt", "content": content, "total_lines": 1000},
    }
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "read"},
        {"role": "assistant", "content": "{'action': 'tool'}"},
        {"role": "tool", "content": str(event)},
    ]

    with use_model_context(recent_messages=[], recent_tool_operations=[]):
        prepared = prepare_model_messages(messages)

    compacted = prepared[-1]["content"]
    assert len(compacted) < 4000
    assert "...[truncated]..." in compacted
    assert messages[-1]["content"] == str(event)
    assert compact_tool_event(event)["result"]["content"] != content


def test_non_structured_tool_message_fails_visibly() -> None:
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "read"},
        {"role": "tool", "content": "not a structured event"},
    ]

    with use_model_context(recent_messages=[], recent_tool_operations=[]):
        try:
            prepare_model_messages(messages)
        except ValueError as exc:
            assert "cannot be compacted" in str(exc)
        else:
            raise AssertionError("expected a visible compaction failure")
