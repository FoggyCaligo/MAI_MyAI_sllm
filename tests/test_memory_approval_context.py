from mai.app.runtime import _previous_assistant_message


def test_previous_assistant_context_uses_latest_existing_assistant_turn() -> None:
    messages = (
        {"role": "assistant", "content": "현재 만년필 구성에 대한 설명"},
        {"role": "user", "content": "eyedropper가 뭐야?"},
        {"role": "user", "content": "네가 말한 내용들은 다 맞아. 방금 질문에 답해줘."},
    )

    assert _previous_assistant_message(messages) == "현재 만년필 구성에 대한 설명"


def test_previous_assistant_context_ignores_non_assistant_messages() -> None:
    messages = (
        {"role": "system", "content": "system"},
        {"role": "tool", "content": "tool result"},
        {"role": "user", "content": "hello"},
    )

    assert _previous_assistant_message(messages) is None
