"""Regression coverage for repeated provisional-answer verification and page budgets."""
from __future__ import annotations

import pytest

from mai.agent.guards import AgentGuard, GuardConfig, NoProgressError
from mai.agent.tool_results import ToolResultStore
from mai.memory.recall.service import DEFAULT_ANCHOR_FACT_LIMIT
from mai.memory.tools import MemorySearchInput
from mai.tools.web import (
    WEB_SEARCH_DEFAULT_RESULTS,
    WEB_SEARCH_SNIPPET_CHARS,
    WebFetchInput,
    WebSearchInput,
)


def test_identical_verifier_defects_are_bounded_independently_of_tool_calls():
    guard = AgentGuard(GuardConfig(max_no_progress_rounds=2))
    defects = [{"code": "claim_grounding_failed", "claims": [("missing_evidence", "A")]}]
    guard.after_verification_rejection(defects)
    guard.after_verification_rejection(defects)
    with pytest.raises(NoProgressError, match="verifier rejected the same structured defects"):
        guard.after_verification_rejection(defects)


def test_changed_structured_verifier_defect_resets_repetition():
    guard = AgentGuard(GuardConfig(max_no_progress_rounds=2))
    a = [{"code": "claim_grounding_failed", "claims": [("missing_evidence", "A")]}]
    b = [{"code": "claim_grounding_failed", "claims": [("missing_evidence", "B")]}]
    guard.after_verification_rejection(a)
    guard.after_verification_rejection(a)
    guard.after_verification_rejection(b)
    guard.after_verification_rejection(b)


def test_smaller_default_web_and_memory_results_preserve_explicit_bounds():
    assert WEB_SEARCH_DEFAULT_RESULTS == 10
    assert WEB_SEARCH_SNIPPET_CHARS == 160
    assert WebSearchInput(query="test").max_results == 10
    assert WebFetchInput(url="https://example.com").max_chars == 33333
    assert MemorySearchInput(limit=8).limit == 8
    with pytest.raises(ValueError, match="limit is required"):
        MemorySearchInput()
    assert DEFAULT_ANCHOR_FACT_LIMIT == 5


def test_result_store_continuation_remains_available_with_reduced_budget():
    store = ToolResultStore(max_inline_chars=10666)
    content = "x" * 24000
    first = store.model_views(content)
    assert first.compact_history_content is not None
    assert len(first.initial_content) <= 10666
    stored_id = next(iter(store._results))
    continuation = store.read(result_id=stored_id, offset=store.max_read_chars, limit=5000)
    assert '"next_offset"' in continuation
    assert "x" in continuation
