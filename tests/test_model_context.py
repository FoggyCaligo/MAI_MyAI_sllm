from __future__ import annotations

from mai.context import compact_tool_event
from mai.model_context import (
    prepare_model_messages,
    use_attachment_evidence,
    use_isolated_model_context,
    use_model_context,
)


def test_recent_dialogue_date_and_working_root_are_injected_before_current_user(tmp_path) -> None:
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "current"},
    ]
    recent = [
        {"role": "user", "content": "previous question"},
        {"role": "assistant", "content": "previous answer"},
    ]
    working_root = str(tmp_path.resolve())

    with use_model_context(
        recent_messages=recent,
        recent_tool_operations=[],
        working_root=working_root,
    ):
        prepared = prepare_model_messages(messages)

    assert "Current date:" in prepared[0]["content"]
    assert f"Conversation working root: {working_root}" in prepared[0]["content"]
    assert prepared[1] == {"role": "user", "content": "previous question"}
    assert prepared[2] == {"role": "assistant", "content": "previous answer"}
    assert prepared[3] == {"role": "user", "content": "current"}
    assert messages[0]["content"] == "system"


def test_isolated_context_suppresses_ambient_chat_tools_root_and_attachments(tmp_path) -> None:
    messages = [
        {"role": "system", "content": "memory phase"},
        {"role": "user", "content": "explicit compact payload"},
    ]
    with use_model_context(
        recent_messages=[{"role": "user", "content": "ambient chat"}],
        recent_tool_operations=[{"tool": "file_read", "arguments": {}, "result": {"value": "ambient tool"}}],
        working_root=str(tmp_path.resolve()),
    ):
        with use_attachment_evidence(
            [{"evidence_id": "attachment:1", "status": "loaded", "content": "ambient attachment"}]
        ):
            with use_isolated_model_context():
                prepared = prepare_model_messages(messages)

    assert len(prepared) == 2
    assert "Current date:" in prepared[0]["content"]
    assert "Conversation working root:" not in prepared[0]["content"]
    assert prepared[1] == {"role": "user", "content": "explicit compact payload"}
    joined = "\n".join(item["content"] for item in prepared)
    assert "ambient chat" not in joined
    assert "ambient tool" not in joined
    assert "ambient attachment" not in joined


def test_attachment_evidence_is_model_context_not_user_text() -> None:
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "original user text"},
    ]
    evidence = [
        {
            "evidence_id": "attachment:1",
            "path": "/tmp/note.txt",
            "status": "loaded",
            "content": "attachment body",
        }
    ]

    with use_model_context(recent_messages=[], recent_tool_operations=[]):
        with use_attachment_evidence(evidence):
            prepared = prepare_model_messages(messages)

    assert prepared[-1] == {"role": "user", "content": "original user text"}
    attachment_context = next(
        item for item in prepared if item["role"] == "system" and "Current attachment evidence" in item["content"]
    )
    assert "attachment:1" in attachment_context["content"]
    assert "attachment body" in attachment_context["content"]
    assert messages[-1]["content"] == "original user text"


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


def test_current_tool_message_is_compacted_into_assistant_context_without_mutating_original() -> None:
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

    assert all(item["role"] != "tool" for item in prepared)
    compacted = prepared[-1]["content"]
    assert prepared[-1]["role"] == "assistant"
    assert "{'action': 'tool'}" in compacted
    assert "Framework tool result:" in compacted
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


def test_tool_message_without_preceding_assistant_action_fails_visibly() -> None:
    event = {"tool": "file_read", "arguments": {}, "result": {"ok": True}}
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "read"},
        {"role": "tool", "content": str(event)},
    ]

    with use_model_context(recent_messages=[], recent_tool_operations=[]):
        try:
            prepare_model_messages(messages)
        except ValueError as exc:
            assert "preceding assistant action" in str(exc)
        else:
            raise AssertionError("expected a visible tool-history contract failure")
