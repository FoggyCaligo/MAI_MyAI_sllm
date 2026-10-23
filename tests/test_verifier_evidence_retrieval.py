"""Targeted verifier evidence retrieval: full corpus, bounded excerpts, provenance."""
from mai.agent.verification import _ClaimSearch, _indexed_evidence, _retrieve_claim_evidence


def test_corpus_preserves_complete_successful_results_and_offsets():
    huge = "alpha " + "Russian text " * 500
    corpus, locations = _indexed_evidence((
        ("file_read", True, None, huge),
        ("terminal_run", False, "Error", "failed output"),
        ("file_search", True, None, "omega"),
    ))
    assert corpus[locations[0]["start"]:locations[0]["end"]] == huge
    assert corpus[locations[1]["start"]:locations[1]["end"]] == "omega"
    assert len(locations) == 2
    assert "failed output" not in corpus


def test_retrieved_evidence_has_exact_source_coordinates():
    corpus, locations = _indexed_evidence((("file_read", True, None, "begin ёлик is human end"),))
    result = _retrieve_claim_evidence(
        [_ClaimSearch(claim="옐리크는 사람이다", queries=["ёлик is human"])], corpus, locations,
    )
    assert result["claim_index"][0]["search_status"] == "located"
    hit = result["lightweight_evidence"][0]
    assert hit["tool_result_index"] == 0
    assert corpus[hit["source_start"]:hit["source_end"]] == hit["text"]
    assert "ёлик is human" in hit["text"]


def test_not_found_is_only_scoped_search_status():
    corpus, locations = _indexed_evidence((("file_read", True, None, "completely different language"),))
    result = _retrieve_claim_evidence(
        [_ClaimSearch(claim="translated detail", queries=["не найдено"])], corpus, locations,
    )
    assert result["claim_index"] == [{"claim": "translated detail", "references": [], "search_status": "not_located_in_selected_scope"}]
    assert result["lightweight_evidence"] == []


def test_results_from_different_tools_keep_distinct_provenance():
    corpus, locations = _indexed_evidence((
        ("file_read", True, None, "Ёлик — человек."),
        ("web_fetch", True, None, "Unrelated page."),
    ))
    result = _retrieve_claim_evidence(
        [_ClaimSearch(claim="He is human", queries=["Ёлик"])], corpus, locations,
    )
    assert result["lightweight_evidence"][0]["tool"] == "file_read"
    assert result["lightweight_evidence"][0]["tool_result_index"] == 0
    assert locations[1]["tool"] == "web_fetch"
