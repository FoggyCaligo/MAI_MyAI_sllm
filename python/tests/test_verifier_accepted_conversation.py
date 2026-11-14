"""Verifier conversation context uses roles without a separate accepted-history evidence channel."""

from mai.agent.verification import FinalGroundingVerifier, _review_payload


def test_review_context_matches_supplied_dialogue_without_second_history_cap():
    history = [
        {"role": "user" if index % 2 == 0 else "assistant", "content": f"conversation {index}"}
        for index in range(14)
    ]
    messages = [*history, {"role": "user", "content": "What remains?"},
                {"role": "tool", "content": "private tool result"},
                {"role": "assistant", "content": "candidate"}]
    payload = _review_payload(candidate="candidate", messages=messages, tool_results=())
    assert payload["conversation_context"] == [*history, {"role": "user", "content": "What remains?"}]
    assert all(item["role"] != "tool" for item in payload["conversation_context"])
    assert "verified_prior_conversation" not in payload
    assert "user_evidence" not in payload
    assert payload["tool_results_in_execution_order"] == []


def test_previous_assistant_messages_are_not_independent_numeric_evidence():
    previous = [{"role": "user", "content": "Prior task"},
                {"role": "assistant", "content": "Confirmed amount is 123456 units."}]
    candidate = "The previously confirmed amount was 123456 units."
    verifier = FinalGroundingVerifier()
    assert "123456" in verifier._unsupported_numeric_facts(
        candidate=candidate,
        messages=[*previous, {"role": "user", "content": "Repeat previous amount"}],
        tool_results=(),
    )


def test_user_role_messages_in_conversation_context_ground_user_stated_numbers():
    verifier = FinalGroundingVerifier()
    assert verifier._unsupported_numeric_facts(
        candidate="The amount you stated was 123456 units.",
        messages=[{"role": "user", "content": "My amount was 123456 units."}],
        tool_results=(),
    ) == ()
