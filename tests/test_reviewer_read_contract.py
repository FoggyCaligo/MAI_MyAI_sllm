"""Decoder schemas reject the invalid actions seen in the uploaded run logs."""
from __future__ import annotations

import asyncio
import json

from jsonschema import Draft202012Validator, ValidationError as SchemaError
from pydantic import ValidationError
import pytest

from mai.agent.tool_results import EvidenceRange, EvidenceSelection, ToolResultStore
from mai.agent.verification import (
    FinalGroundingVerifier, _EvidenceReviewStep, _FactualReviewPayload,
    _FactualNumericReviewPayload, _evidence_step_schema,
)
from mai.llm.models import ModelTurn


def verdict(*, supported=True, numeric=False):
    result = {
        "evidence_verdict": "supported" if supported else "unsupported",
        "action_verdict": "not_applicable", "reasons": [],
        "claims": [] if supported else [{
            "claim": "The offer applies to everyone.", "verdict": "unsupported",
            "defect": "missing_evidence", "reason": "The observed text does not establish eligibility.",
        }],
        "retry_summary": "No correction needed." if supported else "Narrow the eligibility claim.",
    }
    if numeric:
        result.update(numeric_verdict="supported", numeric_reason="The supplied number matches.")
    return result


def read(offset=0, limit=7488):
    return {"read_requests": [{"source_id": "tool:7", "offset": offset, "limit": limit}], "review": None}


@pytest.mark.parametrize("numeric", [False, True])
def test_decoder_and_runtime_agree_on_exclusive_read_or_verdict(numeric):
    model = _EvidenceReviewStep[_FactualNumericReviewPayload if numeric else _FactualReviewPayload]
    schema = _evidence_step_schema(model, [("tool:7", 0, 11561, 7488)])
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    final = {"read_requests": [], "review": verdict(numeric=numeric)}
    for response in [read(), final]:
        validator.validate(response)
        model.model_validate(response, strict=True)
    for response in [
        {"read_requests": read()["read_requests"], "review": verdict(numeric=numeric)},
        {"read_requests": [], "review": None},
    ]:
        with pytest.raises(SchemaError):
            validator.validate(response)
        with pytest.raises(ValidationError):
            model.model_validate(response, strict=True)
    if numeric:
        with pytest.raises(SchemaError):
            validator.validate({"read_requests": [], "review": verdict()})


@pytest.mark.parametrize("source_id,offset,limit", [
    ("tool:7", 0, 7488), ("tool:7", 11561, 1),
    ("tool:7", 7488, 7489), ("tool:unknown", 7488, 1),
])
def test_decoder_read_choices_exclude_cached_starts_unknown_sources_and_invalid_limits(source_id, offset, limit):
    schema = _evidence_step_schema(_EvidenceReviewStep[_FactualReviewPayload], [("tool:7", 7488, 11561, 7488)])
    validator = Draft202012Validator(schema)
    validator.validate(read(7488))
    with pytest.raises(SchemaError):
        validator.validate({"read_requests": [{"source_id": source_id, "offset": offset, "limit": limit}], "review": None})


class Reviewer:
    def __init__(self, responses, *, enforce_decoder=True):
        self.responses = iter(responses)
        self.requests = []
        self.enforce_decoder = enforce_decoder

    async def chat(self, request):
        self.requests.append(request)
        response = next(self.responses)
        if self.enforce_decoder:
            Draft202012Validator(request.response_format).validate(response)
        content = json.dumps(response)
        return ModelTurn(content, "", (), {"role": "assistant", "content": content})


def verify(reviewer, store, result_id):
    # Preserve tool:7 from the failing run rather than renumbering successful sources.
    tools = [("web_fetch", False, "FetchError", "failed")] * 7
    tools.append(("web_fetch", True, None, store.retained_view(result_id)))
    return asyncio.run(FinalGroundingVerifier(reviewer_adapter=reviewer, quality_review_enabled=False).verify(
        candidate="The offer applies to everyone.", messages=[], tool_results=tools,
        evidence_store=store, tool_result_ids=[None] * 7 + [result_id],
    ))


def test_tail_read_marks_source_complete_and_requires_a_real_verdict():
    store = ToolResultStore(max_inline_chars=8000)
    original = "문" * 11561
    result_id = store.capture(original).result_id
    reviewer = Reviewer([read(), read(7488), {"read_requests": [], "review": verdict(supported=False)}])
    result = verify(reviewer, store, result_id)
    assert not result.ok and len(reviewer.requests) == 3
    second = json.loads(reviewer.requests[1].messages[1]["content"])
    assert second["tool_results_in_execution_order"][0]["original_read_state"]["unread_ranges"] == [
        {"offset": 7488, "length": 4073},
    ]
    last_request = reviewer.requests[2]
    last_source = json.loads(last_request.messages[1]["content"])["tool_results_in_execution_order"][0]
    assert last_source["original_read_state"]["complete"]
    assert "".join(item["content"] for item in last_source["reread_excerpts"]) == original
    # A repeated tail is no longer a decoder-permitted response once the
    # original has all been supplied.
    with pytest.raises(SchemaError):
        Draft202012Validator(last_request.response_format).validate(read(7488))


def test_cached_middle_page_is_not_offered_when_later_source_text_remains():
    store = ToolResultStore(max_inline_chars=8000)
    # The log's tool:7 was a 75,671-character result. Repeated offset=7,488
    # reads must not remain decoder choices merely because the source is large.
    result_id = store.capture("문" * 75671).result_id
    reviewer = Reviewer([read(), read(7488), {"read_requests": [], "review": verdict(supported=False)}])
    assert not verify(reviewer, store, result_id).ok
    request = reviewer.requests[2]
    state = json.loads(request.messages[1]["content"])["tool_results_in_execution_order"][0]["original_read_state"]
    assert state["unread_ranges"] == [{"offset": 14976, "length": 60695}]
    assert not state["complete"]
    validator = Draft202012Validator(request.response_format)
    validator.validate(read(14976))
    with pytest.raises(SchemaError):
        validator.validate(read(7488))


def test_retained_ranges_are_already_supplied_and_need_no_extra_read():
    store = ToolResultStore(max_inline_chars=1024)
    source = "Original offer terms."
    result_id = store.capture(source).result_id
    store.retain([EvidenceSelection(result_id=result_id, ranges=[EvidenceRange(offset=0, length=len(source))])])
    reviewer = Reviewer([{"read_requests": [], "review": verdict()}])
    assert verify(reviewer, store, result_id).ok
    assert len(reviewer.requests) == 1
    payload = json.loads(reviewer.requests[0].messages[1]["content"])
    state = payload["tool_results_in_execution_order"][0]["original_read_state"]
    assert state["complete"] and state["unread_ranges"] == []


def test_malformed_action_retry_gets_validation_feedback_and_preserves_rejection():
    store = ToolResultStore(max_inline_chars=8000)
    result_id = store.capture("Observed offer terms.").result_id
    invalid = {"read_requests": read()["read_requests"], "review": verdict()}
    reviewer = Reviewer([invalid, {"read_requests": [], "review": verdict(supported=False)}], enforce_decoder=False)
    assert not verify(reviewer, store, result_id).ok
    assert len(reviewer.requests) == 2
    first, second = reviewer.requests
    assert second.messages[:-1] == first.messages
    assert "Validation errors:" in second.messages[-1]["content"]
    assert json.loads(second.messages[1]["content"]) == json.loads(first.messages[1]["content"])


def test_repeated_malformed_actions_still_exhaust_schema_retry_budget():
    store = ToolResultStore(max_inline_chars=8000)
    result_id = store.capture("Observed offer terms.").result_id
    invalid = {"read_requests": read()["read_requests"], "review": verdict()}
    reviewer = Reviewer([invalid] * 3, enforce_decoder=False)
    with pytest.raises(ValidationError):
        verify(reviewer, store, result_id)
    assert len(reviewer.requests) == 3
    assert len(reviewer.requests[1].messages) == len(reviewer.requests[2].messages)
