"""Regression checks for candidate/evidence separation and main self-review contract."""
from __future__ import annotations

from mai.agent.verification import _FACTUAL_REVIEW_SYSTEM, _review_payload
from mai.app.runtime import AGENT_SYSTEM_PROMPT


def test_full_candidate_is_supplied_for_review():
    answer = "Candidate opening. " + "A" * 9000 + " Candidate ending."
    payload = _review_payload(
        candidate=answer,
        messages=[{"role": "user", "content": "Summarize the evidence."}],
        tool_results=[],
    )
    assert payload["candidate_final"] == answer
    assert payload["current_user_request"] == "Summarize the evidence."


def test_prior_assistant_turn_is_context_not_candidate():
    prior = "Earlier assistant discussed ark and arc."
    candidate = "Left and right refer to political positions."
    payload = _review_payload(
        candidate=candidate,
        messages=[
            {"role": "user", "content": "What does ark mean?"},
            {"role": "assistant", "content": prior},
            {"role": "user", "content": "What do left and right mean?"},
        ],
        tool_results=[],
    )
    assert payload["candidate_final"] == candidate
    assert payload["current_user_request"] == "What do left and right mean?"
    assert {"role": "assistant", "content": prior} in payload["conversation_context"]
    assert "ONLY answer under review is candidate_final" in _FACTUAL_REVIEW_SYSTEM
    assert "Do not evaluate a previous assistant turn" in _FACTUAL_REVIEW_SYSTEM


def test_review_prompt_requires_material_claim_coverage_without_schema_extensions():
    assert "Review all material factual claims in candidate_final" in _FACTUAL_REVIEW_SYSTEM
    assert "Do not invent, reverse" in _FACTUAL_REVIEW_SYSTEM
    assert "unit_reviews" not in _FACTUAL_REVIEW_SYSTEM
    assert "evidence_refs" not in _FACTUAL_REVIEW_SYSTEM


def test_main_agent_uses_pretraining_for_reasoning_not_factual_evidence():
    assert "Pretrained knowledge may be used for reasoning" in AGENT_SYSTEM_PROMPT
    assert "must not be treated as evidence supporting factual claims" in AGENT_SYSTEM_PROMPT
    assert "Before producing a provisional answer" in AGENT_SYSTEM_PROMPT
