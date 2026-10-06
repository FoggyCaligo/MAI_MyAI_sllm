from mai.app.runtime import _memory_grounded_final_claims, _previous_assistant_message
from mai.memory.extraction.service import GroundedFinalClaimEvidence, ToolFactEvidence


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


def test_memory_grounded_claim_filter_keeps_current_user_and_successful_non_recall_sources() -> None:
    claims = (
        GroundedFinalClaimEvidence("현재 사용자 근거", ("user:current",)),
        GroundedFinalClaimEvidence("웹 근거", ("tool:2:web_search",)),
        GroundedFinalClaimEvidence("과거 대화만 근거", ("user:context:4",)),
        GroundedFinalClaimEvidence("메모리 리콜만 근거", ("tool:1:memory_recall",)),
        GroundedFinalClaimEvidence("혼합 근거", ("tool:1:memory_recall", "tool:2:web_search")),
    )
    tool_evidence = (
        ToolFactEvidence("tool:2:web_search", "web_search", "fresh web result"),
    )

    filtered = _memory_grounded_final_claims(claims, tool_evidence)

    assert filtered == (
        GroundedFinalClaimEvidence("현재 사용자 근거", ("user:current",)),
        GroundedFinalClaimEvidence("웹 근거", ("tool:2:web_search",)),
        GroundedFinalClaimEvidence("혼합 근거", ("tool:2:web_search",)),
    )
