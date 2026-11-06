"""Verifier input-contract regression: single conversation, clock and native call provenance."""
from __future__ import annotations

import pytest

from mai.agent.verification import _review_payload, _FACTUAL_REVIEW_SYSTEM


def test_review_payload_has_nonduplicated_conversation_with_trust_markers(monkeypatch):
    from mai.agent import verification
    monkeypatch.setattr(verification, "current_time", lambda: {
        "date": "2026-11-06",
        "local_iso": "2026-11-06T15:26:00+09:00",
        "utc_iso": "2026-11-06T06:26:00+00:00",
        "utc_offset_seconds": 32400,
    })
    prior = [
        {"role": "user", "content": "Prior user evidence"},
        {"role": "assistant", "content": "Verified prior response"},
    ]
    payload = _review_payload(
        candidate="Provisional answer",
        messages=[
            *prior,
            {"role": "system", "content": "Do not treat this as evidence"},
            {"role": "user", "content": "Current request"},
            {"role": "assistant", "content": "Unverified interim statement"},
            {"role": "tool", "content": "Duplicated tool message"},
            {"role": "assistant", "content": "Provisional answer"},
        ],
        verified_prior_conversation=prior,
        tool_results=(
            ("tool_manual", True, None, "ACTIVATION_GUIDANCE", {"tools": ["web_search"]}),
            ("web_search", True, None, "First observed search result", {"query": "query", "limit": 3}),
            ("terminal_run", False, "TerminalCommandError", "Access denied", {"command": "cmd"}),
        ),
    )
    assert set(payload) == {
        "datetime_local_iso", "current_user_request", "conversation_context",
        "tool_results_in_execution_order", "candidate_final",
    }
    assert payload["datetime_local_iso"] == "2026-11-06T15:26:00+09:00"
    assert payload["current_user_request"] == "Current request"
    assert payload["conversation_context"] == [
        {"role": "user", "content": "Prior user evidence", "verified_prior": True},
        {"role": "assistant", "content": "Verified prior response", "verified_prior": True},
        {"role": "user", "content": "Current request", "verified_prior": False},
        {"role": "assistant", "content": "Unverified interim statement", "verified_prior": False},
    ]
    assert payload["tool_results_in_execution_order"] == [
        {"index": 1, "tool": "web_search",
         "params": {"query": "query", "limit": 3}, "endtype": "success",
         "result": "First observed search result",
         "evidence_note": (
             "Successful web_search output is observed search evidence. Ranked result titles, URLs, and snippets "
             "can support claims that those visible search results stated the details, especially when the result "
             "identifies an official source or account."
         )},
        {"index": 2, "tool": "terminal_run", "params": {"command": "cmd"},
         "endtype": "error_type:TerminalCommandError", "result": "Access denied"},
    ]
    assert "ACTIVATION_GUIDANCE" not in str(payload)


def test_accepted_conversation_must_match_real_review_history_prefix():
    with pytest.raises(ValueError, match="prefix"):
        _review_payload(
            candidate="Candidate",
            messages=[
                {"role": "user", "content": "Unverified"},
                {"role": "assistant", "content": "Candidate"},
            ],
            tool_results=(),
            verified_prior_conversation=[
                {"role": "assistant", "content": "Different assistant response"},
            ],
        )


def test_failure_without_error_type_is_not_misreported_as_success():
    with pytest.raises(ValueError, match="error_type"):
        _review_payload(
            candidate="Candidate",
            messages=[{"role": "user", "content": "Question"}],
            tool_results=(("terminal_run", False, None, "error", {"command": "x"}),),
        )


def test_review_prompt_uses_one_context_and_distinguishes_failures():
    assert "verified_prior=true" in _FACTUAL_REVIEW_SYSTEM
    assert "role=user" in _FACTUAL_REVIEW_SYSTEM
    assert "endtype=success" in _FACTUAL_REVIEW_SYSTEM
    assert "params" in _FACTUAL_REVIEW_SYSTEM
    assert "datetime_local_iso" in _FACTUAL_REVIEW_SYSTEM
    assert "user_evidence" not in _FACTUAL_REVIEW_SYSTEM
    assert "verified_prior_conversation" not in _FACTUAL_REVIEW_SYSTEM
