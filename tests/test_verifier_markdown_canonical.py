"""Regression tests for verifier Markdown canonicalization."""
import asyncio
import json
from pathlib import Path
import pytest
from mai.agent.decision_verifier import DecisionGroundingVerifier, DecisionVerifierContractError
from mai.agent.verification_text import canonical_verification_text
from mai.llm.models import ModelTurn


class Extractor:
    def __init__(self, spans):
        self.spans = spans
        self.request = None

    async def chat(self, request):
        self.request = request
        # One category array per rule; Markdown tests use the first category.
        grouped = [list(self.spans), *[[] for _ in range(11)]]
        value = json.dumps(grouped, ensure_ascii=False)
        return ModelTurn(value, "", (), {"role": "assistant", "content": value})


class Judge:
    def __init__(self):
        self.received = None

    async def judge(self, *, candidate, source, claims, on_audit_event=None):
        self.received = (candidate, tuple(claims))
        return ("supported",) * len(claims)


def verify(answer, claims):
    extractor, judge = Extractor(claims), Judge()
    verifier = DecisionGroundingVerifier(
        reviewer_adapter=extractor, judge=judge, quality_review_enabled=False,
    )
    result = asyncio.run(verifier.verify(
        candidate=answer, messages=[{"role": "user", "content": "Explain"}],
        tool_results=(),
    ))
    return result, extractor, judge


def test_emphasis_is_not_part_of_verbatim_contract():
    answer = '**방주**는 원래 **"노아의 방주"**에서 온 말이고, 영어로 **"Ark"**입니다.'
    claim = '방주는 원래 "노아의 방주"에서 온 말이고, 영어로 "Ark"입니다.'
    result, extractor, judge = verify(answer, [claim])
    assert result.ok
    assert json.loads(extractor.request.messages[1]["content"])["candidate_final"] == claim
    assert judge.received == (claim, (claim,))


def test_table_cells_retain_header_relationships():
    answer = "| 단어 | 철자 | 발음 |\n|---|---|---|\n| **Ark** | A-R-K | 아크 |\n| **Arc** | A-R-C | 아크 |"
    canonical = canonical_verification_text(answer)
    assert canonical == "단어: Ark; 철자: A-R-K; 발음: 아크\n단어: Arc; 철자: A-R-C; 발음: 아크"
    result, _, judge = verify(answer, ["단어: Ark; 철자: A-R-K; 발음: 아크"])
    assert result.ok
    assert judge.received[0] == canonical


def test_code_and_escaped_pipe():
    answer = "설명: **굵게**\n\n| 항목 | 값 |\n|---|---|\n| a\\|b | x |\n\n~~~text\nraw **code**\n~~~"
    canonical = canonical_verification_text(answer)
    assert "설명: 굵게" in canonical
    assert "항목: a|b; 값: x" in canonical
    assert "raw **code**" in canonical


def test_semantic_paraphrase_does_not_fail_literal_span_check():
    answer = "**방주**는 Ark라는 영어 단어로 번역됩니다."
    paraphrase = "방주의 영어 표현은 Ark이다."
    result, _, judge = verify(answer, [paraphrase])
    assert result.ok
    assert judge.received == (canonical_verification_text(answer), (paraphrase,))


def test_error_ui_renders_markdown_without_feedback():
    html = Path("mai/app/static/index.html").read_text(encoding="utf-8")
    resume = Path("mai/app/static/resumable-chat.js").read_text(encoding="utf-8")
    assert "typeof data.unverified_answer === 'string' && typeof data.verifier_feedback" not in html
    assert "typeof data.unverified_answer === 'string' && typeof data.verifier_feedback" not in resume
    assert "typeof metadata?.unverified_answer === 'string' && typeof metadata?.verifier_feedback" not in resume
    assert "answer.innerHTML = renderMarkdown(payload.unverified_answer)" in html
