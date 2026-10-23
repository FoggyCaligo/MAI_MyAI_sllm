"""Verifier source-context retrieval contracts."""
import pytest

from mai.agent.verification import _ClaimSearch, _indexed_evidence, _retrieve_claim_evidence


def test_sentence_context_and_title(monkeypatch):
    monkeypatch.setenv("VERIFIER_EVIDENCE_CONTEXT_SENTENCES", "2")
    source = "# Chapter title\nFirst sentence. Second sentence. Third target sentence. Fourth sentence. Fifth sentence. Sixth sentence."
    corpus, positions = _indexed_evidence([("file_read", True, None, source)])
    result = _retrieve_claim_evidence(
        [_ClaimSearch(claim="target assertion", queries=["Third target"])], corpus, positions
    )
    match = result[0]["matches"][0]
    assert match["title"] == "# Chapter title"
    assert "First sentence." in match["context"]
    assert "Fifth sentence." in match["context"]
    assert "Sixth sentence." not in match["context"]
    assert match["tool"] == "file_read"
    assert corpus[match["start"]:match["end"]] == match["context"]


def test_sentence_neighbor_setting(monkeypatch):
    monkeypatch.setenv("VERIFIER_EVIDENCE_CONTEXT_SENTENCES", "0")
    source = "One. Two target. Three."
    corpus, positions = _indexed_evidence([("file_read", True, None, source)])
    match = _retrieve_claim_evidence([_ClaimSearch(claim="Two", queries=["Two"])], corpus, positions)[0]["matches"][0]
    assert match["context"].strip() == "Two target."


def test_secondary_case_insensitive_retrieval(monkeypatch):
    monkeypatch.setenv("VERIFIER_EVIDENCE_CONTEXT_SENTENCES", "1")
    corpus, positions = _indexed_evidence([("memory_recall", True, None, "Stored name: SHIN JAEYONG.")])
    match = _retrieve_claim_evidence([_ClaimSearch(claim="user name", queries=["shin jaeyong"])], corpus, positions)[0]["matches"][0]
    assert match["retrieval_strategy"] == "case_insensitive"


def test_bad_setting_fails_explicitly(monkeypatch):
    monkeypatch.setenv("VERIFIER_EVIDENCE_CONTEXT_SENTENCES", "broken")
    corpus, positions = _indexed_evidence([("memory_recall", True, None, "Example.")])
    with pytest.raises(ValueError):
        _retrieve_claim_evidence([_ClaimSearch(claim="Example", queries=["Example"])], corpus, positions)
