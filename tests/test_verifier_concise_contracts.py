"""Stage A/B/C output constraints and indexed corpus diagnostics."""
import pytest
from pydantic import ValidationError

from mai.agent.verification import (
    _ClaimSearch,
    _ClaimReviewPayload,
    _FinalReviewPayload,
    _indexed_evidence,
    _retrieve_claim_evidence,
)


def test_claim_query_limit_is_structurally_validated():
    assert len(_ClaimSearch(claim="A factual assertion", queries=["one", "two", "three", "four", "five"]).queries) == 5
    with pytest.raises(ValidationError):
        _ClaimSearch(claim="A factual assertion", queries=["a", "b", "c", "d", "e", "f"])
    with pytest.raises(ValidationError):
        _ClaimSearch(claim="A factual assertion", queries=[])


def test_long_rejection_text_is_protocol_violation():
    with pytest.raises(ValidationError):
        _ClaimReviewPayload(claim="One", verdict="unsupported", defect="missing_evidence", reason="x" * 281)
    with pytest.raises(ValidationError):
        _FinalReviewPayload(evidence_verdict="unsupported", reasons=[], claims=[], retry_summary="x" * 281)


def test_deduplicated_evidence_is_shorter_than_repeated_windows(monkeypatch):
    monkeypatch.setenv("VERIFIER_EVIDENCE_CONTEXT_CHARS", "500")
    corpus, sources = _indexed_evidence([("file_read", True, None, "prefix " + ("a" * 200) + " name=Example age=26 " + ("b" * 200))])
    result = _retrieve_claim_evidence([
        _ClaimSearch(claim="Name", queries=["name=Example"]),
        _ClaimSearch(claim="Age", queries=["age=26"]),
    ], corpus, sources)
    assert len(result["lightweight_evidence"]) == 1
    assert len(result["claim_index"]) == 2
    assert len(result["lightweight_evidence"][0]["text"]) <= len(corpus)
