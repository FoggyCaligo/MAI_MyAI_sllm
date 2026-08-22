from pathlib import Path


def test_model_context_contract_documents_raw_chat_graph_separation() -> None:
    text = Path("docs/contracts/MODEL_CONTEXT_CONTRACT.md").read_text(encoding="utf-8")
    assert "recent conversation = short-term conversational context" in text
    assert "semantic graph      = durable semantic memory" in text
    assert "Raw chat history 전체를 자동으로 graph edge 집합으로 복제하지 않는다" in text


def test_roadmap_includes_live_graph_memory_contract() -> None:
    text = Path("ROADMAP.md").read_text(encoding="utf-8")
    for phrase in (
        "최근 대화 context",
        "tool result compaction",
        "recent tool-operation context",
        "current date system injection",
        "Mandatory vector recall",
        "ViewedGraph",
        "memory/generate/node",
        "memory/fix/edge",
        "graph_synced: true",
        "UNIQUE(user_id, start_node_id, end_node_id)",
        "MAI_OLLAMA_EMBEDDING_MODEL",
        "기존 `data/graph.sqlite3`은 삭제하고 새 schema로 재생성",
        "owner/trial별 tool 제한",
        "persistent authenticated session",
        "attachment automatic read/analyze",
        "Source provenance",
    ):
        assert phrase in text
