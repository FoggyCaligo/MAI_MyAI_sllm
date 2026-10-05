from mai.agent.verification import _FINAL_REVIEW_SYSTEM as FINAL_REVIEW_PROMPT



def test_final_reviewer_checks_temporal_consistency() -> None:
    assert "temporal framing is consistent with the current date/time" in FINAL_REVIEW_PROMPT
    assert "dates or timestamps established by the supplied evidence" in FINAL_REVIEW_PROMPT
