from __future__ import annotations

from mai.model_context import prepare_model_messages, use_model_context


def test_prior_chat_and_tool_context_are_separate_from_current_user_message() -> None:
    prior_chat = [
        {"role": "user", "content": "내 이름은 철수야"},
        {"role": "assistant", "content": "알겠어"},
    ]
    prior_tools = [
        {
            "tool": "file_search",
            "arguments": {"pattern": "*.py"},
            "result": {"count": 3, "files": ["a.py", "b.py", "c.py"]},
        }
    ]
    current = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "내 이름이 뭐였지?"},
    ]

    with use_model_context(recent_messages=prior_chat, recent_tool_operations=prior_tools):
        prepared = prepare_model_messages(current)

    roles_and_content = [(item["role"], item["content"]) for item in prepared]
    assert roles_and_content[-1] == ("user", "내 이름이 뭐였지?")
    assert ("user", "내 이름은 철수야") in roles_and_content
    assert ("assistant", "알겠어") in roles_and_content
    assert any(
        role == "system" and "Recent tool operations from earlier turns" in content
        for role, content in roles_and_content
    )
