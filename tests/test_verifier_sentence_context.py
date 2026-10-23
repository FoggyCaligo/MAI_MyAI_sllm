"""Deduplicated, source-faithful verifier evidence windows."""
import pytest

from mai.agent.verification import _ClaimSearch, _indexed_evidence, _retrieve_claim_evidence


def test_window_merges_overlaps_and_preserves_title(monkeypatch):
    monkeypatch.setenv("VERIFIER_EVIDENCE_CONTEXT_CHARS", "30")
    source = "# Chapter\n" + ("a" * 100) + "first value " + ("b" * 10) + "second value" + ("c" * 100)
    corpus, locations = _indexed_evidence([("file_read", True, None, source)])
    result = _retrieve_claim_evidence([
        _ClaimSearch(claim="first", queries=["first value"]),
        _ClaimSearch(claim="second", queries=["second value"]),
    ], corpus, locations)
    assert len(result["lightweight_evidence"]) == 1
    evidence = result["lightweight_evidence"][0]
    assert evidence["title"] == "# Chapter"
    assert evidence["text"] == corpus[evidence["source_start"]:evidence["source_end"]]
    assert evidence["tool"] == "file_read"
    assert result["claim_index"][0]["references"][0]["evidence_id"] == evidence["id"]
    assert result["claim_index"][1]["references"][0]["evidence_id"] == evidence["id"]


def test_zero_character_context(monkeypatch):
    monkeypatch.setenv("VERIFIER_EVIDENCE_CONTEXT_CHARS", "0")
    corpus, locations = _indexed_evidence([("file_read", True, None, "before target after")])
    result = _retrieve_claim_evidence([_ClaimSearch(claim="target", queries=["target"])], corpus, locations)
    assert result["lightweight_evidence"][0]["text"] == "target"


def test_case_insensitive_search(monkeypatch):
    monkeypatch.setenv("VERIFIER_EVIDENCE_CONTEXT_CHARS", "3")
    corpus, locations = _indexed_evidence([("memory_recall", True, None, "SHIN JAEYONG")])
    result = _retrieve_claim_evidence([_ClaimSearch(claim="name", queries=["shin jaeyong"])], corpus, locations)
    assert result["claim_index"][0]["references"][0]["retrieval_strategy"] == "case_insensitive"


def test_invalid_setting_is_not_silently_ignored(monkeypatch):
    monkeypatch.setenv("VERIFIER_EVIDENCE_CONTEXT_CHARS", "broken")
    corpus, locations = _indexed_evidence([("file_read", True, None, "something")])
    with pytest.raises(ValueError):
        _retrieve_claim_evidence([_ClaimSearch(claim="something", queries=["something"])], corpus, locations)
