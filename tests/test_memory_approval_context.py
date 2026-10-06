from mai.agent.verification import GroundedClaim
from mai.app.runtime import _memory_grounded_final_claims, _previous_assistant_message
from mai.memory.extraction.service import GroundedFinalClaimEvidence


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


def test_memory_grounded_claim_requires_all_support_ids_to_be_admissible() -> None:
    claims = (
        GroundedClaim("웹으로 확인된 사실", ("tool:2:web_search",)),
        GroundedClaim("과거 user context와 웹을 함께 요구하는 사실", ("user:1:0", "tool:2:web_search")),
        GroundedClaim("현재 user 직접 주장", ("user:7:0",)),
        GroundedClaim("현재시각에만 의존하는 사실", ("runtime:current_time",)),
    )

    filtered = _memory_grounded_final_claims(
        claims,
        admissible_support_ids={"tool:2:web_search", "user:7:0"},
    )

    assert filtered == (
        GroundedFinalClaimEvidence("웹으로 확인된 사실", ("tool:2:web_search",)),
        GroundedFinalClaimEvidence("현재 user 직접 주장", ("user:7:0",)),
    )
