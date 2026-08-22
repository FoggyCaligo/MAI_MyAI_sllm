from pathlib import Path


def test_model_context_contract_documents_raw_chat_graph_separation() -> None:
    text = Path("docs/contracts/MODEL_CONTEXT_CONTRACT.md").read_text(encoding="utf-8")
    assert "recent conversation = short-term conversational context" in text
    assert "semantic graph      = durable semantic memory" in text
    assert "Raw chat history 전체를 자동으로 graph edge 집합으로 복제하지 않는다" in text


def test_roadmap_includes_required_mk4_parity_work() -> None:
    text = Path("ROADMAP.md").read_text(encoding="utf-8")
    for phrase in (
        "최근 대화 context",
        "tool result compaction",
        "recent tool-operation context",
        "current date system injection",
        "동일 successful action 반복 계약",
        "Autonomy retry",
        "web evidence grounding pass",
        "owner/trial별 tool 제한",
        "persistent authenticated session",
        "session별 file/code working context/root",
        "attachment automatic read/analyze",
        "model-managed `scratchpad_put` / `scratchpad_update`",
        "Graph source provenance",
    ):
        assert phrase in text
