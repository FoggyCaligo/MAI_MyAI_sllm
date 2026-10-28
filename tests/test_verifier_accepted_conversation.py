"""Verifier may rely on previously accepted answers without inherited tool logs."""

from mai.agent.verification import FinalGroundingVerifier, _review_payload


def test_review_context_matches_supplied_dialogue_without_second_history_cap():
    history = [
        {"role": "user" if index % 2 == 0 else "assistant", "content": f"conversation {index}"}
        for index in range(14)
    ]
    messages = [*history, {"role": "user", "content": "What remains?"},
                {"role": "tool", "content": "private tool result"},
                {"role": "assistant", "content": "candidate"}]
    payload = _review_payload(
        candidate="candidate", messages=messages, tool_results=(),
        verified_prior_conversation=history,
    )
    assert len(payload["verified_prior_conversation"]) == len(history)
    assert payload["verified_prior_conversation"] == history
    assert payload["conversation_context"] == [*history, {"role": "user", "content": "What remains?"}]
    assert all(item["role"] != "tool" for item in payload["conversation_context"])
    assert payload["tool_results_in_execution_order"] == []


def test_accepted_answers_support_numeric_claims_but_untrusted_history_does_not():
    previous = [{"role": "user", "content": "Prior task"},
                {"role": "assistant", "content": "Confirmed amount is 123456 units."}]
    candidate = "The previously confirmed amount was 123456 units."
    verifier = FinalGroundingVerifier(verified_prior_conversation=previous)
    assert verifier._unsupported_numeric_facts(
        candidate=candidate,
        messages=[{"role": "user", "content": "Repeat previous amount"}],
        tool_results=(),
    ) == ()
    untrusted = FinalGroundingVerifier()
    assert "123456" in untrusted._unsupported_numeric_facts(
        candidate=candidate,
        messages=[*previous, {"role": "user", "content": "Repeat previous amount"}],
        tool_results=(),
    )


def test_prior_conversation_rejects_invalid_roles():
    import pytest
    with pytest.raises(ValueError):
        FinalGroundingVerifier(
            verified_prior_conversation=[{"role": "tool", "content": "untrusted"}],
        )
