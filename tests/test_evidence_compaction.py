"""Selected source ranges retain provenance; reviewers can check omitted originals."""
from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import BaseModel, ValidationError

from mai.agent import AgentRuntime
from mai.agent.loop import AgentLoop
from mai.agent.tool_results import (
    EvidenceRange, EvidenceSelection, ToolResultReadLimitError, ToolResultStore,
)
from mai.agent.verification import (
    FinalGroundingVerifier, FinalVerificationResult, ReviewerEvidenceReadError,
    _NUMERIC_REVIEW_SYSTEM,
)
from mai.llm.models import ModelTurn, NativeToolCall
from mai.tools.registry import ToolRegistry


def turn(content="", calls=()):
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = [
            {"type": "function", "function": {"name": call.name, "arguments": call.arguments}}
            for call in calls
        ]
    return ModelTurn(content=content, thinking="", tool_calls=tuple(calls), assistant_message=message)


def selected(store, source, offset, length):
    view = store.capture(source)
    store.retain([EvidenceSelection(result_id=view.result_id,
        ranges=[EvidenceRange(offset=offset, length=length)])])
    return view.result_id


def test_verbatim_unicode_ranges_and_atomic_validation():
    store = ToolResultStore(max_inline_chars=1024)
    source = "앞말🙂선택한 원문\n뒤의 조건"
    result_id = selected(store, source, 3, 6)
    before = store.retained_view(result_id)
    assert json.loads(before)["excerpts"][0]["content"] == source[3:9]
    with pytest.raises(ValueError, match="exceed"):
        store.retain([
            EvidenceSelection(result_id=result_id, ranges=[]),
            EvidenceSelection(result_id=store.store("short").result_id,
                              ranges=[EvidenceRange(offset=0, length=99)]),
        ])
    assert store.retained_view(result_id) == before
    assert store.original(result_id).content == source


def test_retained_budget_is_an_error_instead_of_silent_truncation():
    store = ToolResultStore(max_inline_chars=1024)
    result_id = store.capture("x" * 3000).result_id
    with pytest.raises(ToolResultReadLimitError):
        store.retain([EvidenceSelection(result_id=result_id,
            ranges=[EvidenceRange(offset=0, length=2000)])])
    assert json.loads(store.retained_view(result_id))["excerpts"] == []
    assert len(store.original(result_id).content) == 3000


def test_main_round_and_verifier_receive_only_retained_ranges():
    class EchoInput(BaseModel):
        text: str

    source = "kept statement. " + "unselected original context " * 50
    store = ToolResultStore(max_inline_chars=2048)
    registry = ToolRegistry()
    registry.add(name="echo", description="Return text", input_model=EchoInput, handler=lambda text: text)

    class Main:
        def __init__(self):
            self.round = 0
            self.result_id = None

        async def chat(self, request):
            self.round += 1
            if self.round == 1:
                return turn(calls=[NativeToolCall("tool_manual", {"tools": ["echo"]})])
            if self.round == 2:
                return turn(calls=[NativeToolCall("echo", {"text": source})])
            echoes = [m for m in request.messages if m.get("tool_name") == "echo"]
            if self.round == 3:
                header, body = echoes[0]["content"].split("\n", 1)
                self.result_id = json.loads(header)["result_id"]
                assert body == source
                return turn(calls=[
                    NativeToolCall("tool_manual", {"tools": [], "retain_evidence": [
                        {"result_id": self.result_id, "ranges": [{"offset": 0, "length": 15}]},
                    ]}),
                    NativeToolCall("echo", {"text": "new observation"}),
                ])
            assert self.round == 4
            compact = json.loads(echoes[0]["content"])
            assert compact["excerpts"][0]["content"] == source[:15]
            assert "unselected original context" not in echoes[0]["content"]
            return turn("kept statement.")

    class Verifier:
        async def verify(self, **kwargs):
            result = json.loads(kwargs["tool_results"][1][3])
            assert result["excerpts"][0]["content"] == source[:15]
            assert "unselected original context" not in kwargs["tool_results"][1][3]
            assert kwargs["evidence_store"].original(kwargs["tool_result_ids"][1]).content == source
            return FinalVerificationResult(ok=True)

    main = Main()
    result = asyncio.run(AgentRuntime(main, registry, tool_result_store=store,
        final_verifier=Verifier()).run_user_message("Summarize the source"))
    assert result.model_rounds == 4
    assert result.tool_executions[1].audit_content == source


def factual(*, supported=True, quote=None):
    return {
        "evidence_verdict": "supported" if supported else "unsupported",
        "action_verdict": "not_applicable", "reasons": [],
        "claims": [] if supported else [{
            "claim": "The offer is unconditional", "verdict": "unsupported",
            "defect": "contradiction", "reason": "The original contains a condition.",
            "evidence_refs": [{"source_id": "tool:0", "location": "reread_excerpts[0]",
                               "quote": quote}],
        }],
        "retry_summary": "No correction needed." if supported else "Preserve the original condition.",
    }


class Reviewer:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    async def chat(self, request):
        self.requests.append(request)
        return turn(json.dumps(next(self.responses)))


def test_reviewer_can_find_omitted_contradiction_under_original_source_id():
    store = ToolResultStore(max_inline_chars=1024)
    source = "Offer available. Only for existing customers."
    result_id = selected(store, source, 0, 16)
    reviewer = Reviewer([
        {"review": None, "read_requests": [{"source_id": "tool:0", "offset": 17, "limit": 28}]},
        {"review": factual(supported=False, quote="Only for existing customers."), "read_requests": []},
    ])
    result = asyncio.run(FinalGroundingVerifier(reviewer_adapter=reviewer, quality_review_enabled=False).verify(
        candidate="The offer is unconditional.", messages=[{"role": "user", "content": "Check the offer."}],
        tool_results=[("web_fetch", True, None, store.retained_view(result_id))],
        evidence_store=store, tool_result_ids=[result_id],
    ))
    assert not result.ok
    assert result.review_details["claims"][0]["evidence_refs"][0]["source_id"] == "tool:0"
    first = json.loads(reviewer.requests[0].messages[1]["content"])
    second = json.loads(reviewer.requests[1].messages[1]["content"])
    assert "Only for existing customers." not in first["tool_results_in_execution_order"][0]["result"]
    assert second["tool_results_in_execution_order"][0]["reread_excerpts"][0]["content"] == source[17:45]
    assert len(second["tool_results_in_execution_order"]) == 1
    assert all(request.tools == () and request.think is False for request in reviewer.requests)


@pytest.mark.parametrize("source_id", ["tool:1", "tool:invented"])
def test_reviewer_cannot_read_failed_or_unknown_sources(source_id):
    store = ToolResultStore(max_inline_chars=1024)
    good = store.capture("valid observed source").result_id
    failed = store.capture("failed output").result_id
    reviewer = Reviewer([{"review": None, "read_requests": [
        {"source_id": source_id, "offset": 0, "limit": 5},
    ]}])
    with pytest.raises(ReviewerEvidenceReadError):
        asyncio.run(FinalGroundingVerifier(reviewer_adapter=reviewer).verify(
            candidate="An assertion.", messages=[],
            tool_results=[("web_fetch", True, None, store.retained_view(good)),
                          ("web_fetch", False, "Failure", store.retained_view(failed))],
            evidence_store=store, tool_result_ids=[good, failed],
        ))
    assert len(reviewer.requests) == 1


@pytest.mark.parametrize("second_offset,second_limit", [(0, 5), (1, 3)])
def test_persistent_no_progress_read_fails_after_an_explicit_notice(second_offset, second_limit):
    store = ToolResultStore(max_inline_chars=1024)
    result_id = store.capture("original source").result_id
    read = {"review": None, "read_requests": [{"source_id": "tool:0", "offset": 0, "limit": 5}]}
    repeated = {"review": None, "read_requests": [
        {"source_id": "tool:0", "offset": second_offset, "limit": second_limit},
    ]}
    reviewer = Reviewer([read, repeated, repeated])
    with pytest.raises(ReviewerEvidenceReadError):
        asyncio.run(FinalGroundingVerifier(reviewer_adapter=reviewer).verify(
            candidate="A claim.", messages=[], tool_results=[("web_fetch", True, None, store.retained_view(result_id))],
            evidence_store=store, tool_result_ids=[result_id],
        ))
    assert len(reviewer.requests) == 3
    notice = json.loads(reviewer.requests[2].messages[1]["content"])["original_read_status"]
    assert notice["results"][0]["status"] == "already_provided"
    assert notice["consecutive_no_progress_rounds"] == 1


def test_duplicate_and_overlapping_requests_in_one_batch_are_deduplicated():
    store = ToolResultStore(max_inline_chars=1024)
    source = "original source with conditions"
    result_id = store.capture(source).result_id
    reviewer = Reviewer([
        {"review": None, "read_requests": [
            {"source_id": "tool:0", "offset": 0, "limit": 10},
            {"source_id": "tool:0", "offset": 0, "limit": 10},
            {"source_id": "tool:0", "offset": 1, "limit": 3},
            {"source_id": "tool:0", "offset": 8, "limit": 10},
        ]},
        {"review": factual(), "read_requests": []},
    ])
    result = asyncio.run(FinalGroundingVerifier(reviewer_adapter=reviewer, quality_review_enabled=False).verify(
        candidate="A claim.", messages=[], tool_results=[("web_fetch", True, None, store.retained_view(result_id))],
        evidence_store=store, tool_result_ids=[result_id],
    ))
    assert result.ok and len(reviewer.requests) == 2
    payload = json.loads(reviewer.requests[1].messages[1]["content"])
    excerpts = payload["tool_results_in_execution_order"][0]["reread_excerpts"]
    assert [(item["offset"], item["returned_chars"]) for item in excerpts] == [(0, 10), (10, 8)]
    assert "".join(item["content"] for item in excerpts) == source[:18]
    assert [item["new_chars"] for item in payload["original_read_status"]["results"]] == [10, 0, 0, 8]


def test_one_cached_read_notice_can_be_followed_by_a_valid_verdict():
    store = ToolResultStore(max_inline_chars=1024)
    result_id = store.capture("complete source").result_id
    read = {"review": None, "read_requests": [{"source_id": "tool:0", "offset": 0, "limit": 512}]}
    reviewer = Reviewer([read, read, {"review": factual(), "read_requests": []}])
    result = asyncio.run(FinalGroundingVerifier(reviewer_adapter=reviewer, quality_review_enabled=False).verify(
        candidate="A claim.", messages=[], tool_results=[("web_fetch", True, None, store.retained_view(result_id))],
        evidence_store=store, tool_result_ids=[result_id],
    ))
    assert result.ok and len(reviewer.requests) == 3
    payload = json.loads(reviewer.requests[2].messages[1]["content"])
    assert len(payload["tool_results_in_execution_order"][0]["reread_excerpts"]) == 1
    assert payload["original_read_status"]["results"][0]["status"] == "already_provided"


def test_new_source_characters_reset_the_no_progress_budget():
    store = ToolResultStore(max_inline_chars=1024)
    result_id = store.capture("complete source").result_id
    def read(offset):
        return {"review": None, "read_requests": [{"source_id": "tool:0", "offset": offset, "limit": 5}]}
    reviewer = Reviewer([read(0), read(0), read(5), read(5), {"review": factual(), "read_requests": []}])
    result = asyncio.run(FinalGroundingVerifier(reviewer_adapter=reviewer, quality_review_enabled=False).verify(
        candidate="A claim.", messages=[], tool_results=[("web_fetch", True, None, store.retained_view(result_id))],
        evidence_store=store, tool_result_ids=[result_id],
    ))
    assert result.ok
    progressed = json.loads(reviewer.requests[3].messages[1]["content"])
    assert progressed["original_read_status"]["consecutive_no_progress_rounds"] == 0


def test_reviewer_failure_preserves_unverified_draft_with_the_error():
    from mai.agent import AgentRunFailure
    class Main:
        async def chat(self, request):
            return turn("The provisional explanation.")
    class FailingVerifier:
        async def verify(self, **kwargs):
            raise ReviewerEvidenceReadError("original read progress budget exhausted")
    with pytest.raises(AgentRunFailure) as failure:
        asyncio.run(AgentRuntime(Main(), ToolRegistry(), final_verifier=FailingVerifier()).run_user_message("Explain."))
    assert failure.value.error_type == "ReviewerEvidenceReadError"
    assert failure.value.context.last_rejected_draft == "The provisional explanation."
    assert "The provisional explanation." in failure.value.user_visible_error
    assert "확정 답변이 아닙니다" in failure.value.user_visible_error


def test_flagged_numeric_and_factual_verdicts_share_one_model_call():
    review = factual()
    review.update(numeric_verdict="unsupported", numeric_reason="The source establishes a different price.")
    reviewer = Reviewer([review])
    result = asyncio.run(FinalGroundingVerifier(reviewer_adapter=reviewer, quality_review_enabled=False).verify(
        candidate="The price is 72,000 won.", messages=[{"role": "user", "content": "The price is 70,000 won."}],
        tool_results=[],
    ))
    assert not result.ok
    assert [issue.code for issue in result.issues] == ["numeric_grounding_failed"]
    assert len(reviewer.requests) == 1
    assert _NUMERIC_REVIEW_SYSTEM in reviewer.requests[0].messages[0]["content"]
    assert json.loads(reviewer.requests[0].messages[1]["content"])["flagged_numeric_or_date_values"] == ["72000"]


def test_python_numeric_checker_exceptions_remain_errors(monkeypatch):
    verifier = FinalGroundingVerifier(reviewer_adapter=Reviewer([]))
    def fail(**kwargs):
        raise ValueError("numeric checker contract violated")
    monkeypatch.setattr(verifier, "_unsupported_numeric_facts", fail)
    with pytest.raises(ValueError, match="numeric checker contract violated"):
        asyncio.run(verifier.verify(candidate="A claim.", messages=[], tool_results=[]))


def test_missing_combined_numeric_verdict_is_not_accepted():
    reviewer = Reviewer([factual()] * 3)
    with pytest.raises(ValidationError):
        asyncio.run(FinalGroundingVerifier(reviewer_adapter=reviewer, quality_review_enabled=False).verify(
            candidate="가격은 72,000원.", messages=[{"role": "user", "content": "가격은 70,000원."}],
            tool_results=[],
        ))
    assert len(reviewer.requests) == 3  # existing schema retry budget, no successful verdict


def test_invalid_retention_does_not_partially_activate_tools():
    class Input(BaseModel):
        text: str
    registry = ToolRegistry()
    registry.add(name="echo", description="Echo", input_model=Input, handler=lambda text: text)
    store = ToolResultStore(max_inline_chars=1024)
    loop = AgentLoop(None, registry, tool_result_store=store)
    activated = set()
    execution = asyncio.run(loop._execute_tool(
        NativeToolCall("tool_manual", {"tools": ["echo"], "retain_evidence": [
            {"result_id": "not-observed", "ranges": [{"offset": 0, "length": 1}]},
        ]}), available_tool_names=frozenset({"tool_manual"}), activated_tool_names=activated,
    ))
    assert not execution.ok
    assert execution.error_type == "ToolResultNotFoundError"
    assert activated == set()


def test_main_original_read_keeps_original_id_and_single_pagination_header():
    from mai.agent.tool_results import register_tool_result_tools
    store = ToolResultStore(max_inline_chars=1024)
    result_id = store.capture("original characters " * 100).result_id
    registry = ToolRegistry()
    register_tool_result_tools(registry, store)
    loop = AgentLoop(None, registry, tool_result_store=store)
    execution = asyncio.run(loop._execute_tool(
        NativeToolCall("tool_result_read", {"result_id": result_id, "offset": 50, "limit": 80}),
        available_tool_names=frozenset({"tool_result_read"}), activated_tool_names={"tool_result_read"},
    ))
    header, body = execution.content.split("\n", 1)
    assert execution.ok and execution.result_id == result_id
    assert json.loads(header)["result_id"] == result_id
    assert body == store.original(result_id).content[50:130]


def test_compacted_numeric_check_uses_original_values_not_length_metadata():
    store = ToolResultStore(max_inline_chars=1024)
    result_id = store.capture("The observed price is 70000 won.").result_id
    review = factual()
    reviewer = Reviewer([{"review": review, "read_requests": []}])
    result = asyncio.run(FinalGroundingVerifier(reviewer_adapter=reviewer, quality_review_enabled=False).verify(
        candidate="The price is 70000 won.", messages=[],
        tool_results=[("web_fetch", True, None, store.retained_view(result_id))],
        evidence_store=store, tool_result_ids=[result_id],
    ))
    assert result.ok
    assert _NUMERIC_REVIEW_SYSTEM not in reviewer.requests[0].messages[0]["content"]


def test_supported_numeric_verdict_cannot_override_factual_rejection():
    review = factual(supported=False, quote="Only for existing customers.")
    review.update(numeric_verdict="supported", numeric_reason="Equivalent date formatting.")
    reviewer = Reviewer([review])
    result = asyncio.run(FinalGroundingVerifier(reviewer_adapter=reviewer, quality_review_enabled=False).verify(
        candidate="The offer is unconditional through 2026년.", messages=[], tool_results=[],
    ))
    assert not result.ok
    assert [issue.code for issue in result.issues] == ["claim_grounding_failed"]
    assert result.review_details["numeric_verdict"] == "supported"


def test_numeric_and_factual_review_can_reopen_original_in_same_phase():
    store = ToolResultStore(max_inline_chars=1024)
    result_id = store.capture("The price is 70,000 won.").result_id
    review = factual()
    review.update(numeric_verdict="unsupported", numeric_reason="The original states 70,000.")
    reviewer = Reviewer([
        {"review": None, "read_requests": [{"source_id": "tool:0", "offset": 0, "limit": 100}]},
        {"review": review, "read_requests": []},
    ])
    result = asyncio.run(FinalGroundingVerifier(reviewer_adapter=reviewer, quality_review_enabled=False).verify(
        candidate="The price is 72,000 won.", messages=[],
        tool_results=[("web_fetch", True, None, store.retained_view(result_id))],
        evidence_store=store, tool_result_ids=[result_id],
    ))
    assert not result.ok and result.issues[0].code == "numeric_grounding_failed"
    assert len(reviewer.requests) == 2
    assert all(_NUMERIC_REVIEW_SYSTEM in req.messages[0]["content"] for req in reviewer.requests)
