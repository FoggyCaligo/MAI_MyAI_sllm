from pathlib import Path

from MK5.core.graph.anchors import user_anchor_id
from MK5.core.graph.repository import GraphRepository
from MK5.core.graph.service import GraphMemoryService
from MK5.core.graph.text_graph import tokenize_spans


def test_user_anchor_is_persistent_key() -> None:
    assert user_anchor_id("alice") == "user_anchor::alice"


def test_record_user_utterance_exposes_memory_summary() -> None:
    repo = GraphRepository(":memory:")
    service = GraphMemoryService(repo)

    service.record_user_utterance(
        user_id="alice",
        text="I build user interfaces. I enjoy TypeScript.",
        session_id="s1",
    )

    summary = service.user_memory_summary("alice")
    assert any("I build user interfaces." in item["label"] for item in summary)
    assert any("I enjoy TypeScript." in item["label"] for item in summary)
    assert all(set(item["subgraph"]) >= {"focus", "relations"} for item in summary)
    assert all(len(item["subgraph"]["relations"]) <= 4 for item in summary)
    repo.close()


def test_record_user_utterance_graphizes_tokens_into_concepts() -> None:
    repo = GraphRepository(":memory:")
    service = GraphMemoryService(repo)

    service.record_user_utterance(
        user_id="alice",
        text="Frontend developers build interfaces.",
        session_id="s1",
    )

    concept_results = service.graph_search(user_id="alice", query="frontend", limit=8)
    assert any(item["focus"]["node_type"] == "concept" for item in concept_results)

    relations = {(edge.source_id, edge.target_id, edge.relation) for edge in repo.all_edges()}
    assert any(relation == "user_mentions_concept" for _, _, relation in relations)
    assert any(relation == "user_adjacent_concept" for _, _, relation in relations)
    repo.close()


def test_record_user_utterance_uses_sentence_breaker_segments() -> None:
    repo = GraphRepository(":memory:")
    service = GraphMemoryService(repo)

    service.record_user_utterance(
        user_id="alice",
        text="엄마",
        session_id="s1",
    )
    service.record_user_utterance(
        user_id="alice",
        text="엄마가 아이를 안는다.",
        session_id="s1",
    )

    concept_labels = {
        label
        for node in repo.all_nodes()
        if node.node_type == "concept"
        for label in node.labels
    }
    assert "엄마" in concept_labels
    repo.close()


def test_korean_concept_labels_are_not_suffix_stripped_by_text_graph() -> None:
    repo = GraphRepository(":memory:")
    service = GraphMemoryService(repo)

    service.record_user_utterance(
        user_id="alice",
        text="글록의 특징과 총기시장에서의 의의",
        session_id="s1",
    )

    labels = {
        label
        for node in repo.all_nodes()
        if node.node_type == "concept"
        for label in node.labels
    }
    assert labels
    assert "글록의" in labels or "글록" in labels
    repo.close()


def test_contiguous_sentence_breaker_fragments_are_rejoined() -> None:
    labels = [span.normalized for span in tokenize_spans("강지라는 스트리머의 활동기간을 찾아줄래?")]

    assert "스트리머의" in labels
    assert "스트" not in labels
    assert "리머" not in labels


def test_graph_search_returns_small_subgraph_summaries() -> None:
    repo = GraphRepository(":memory:")
    service = GraphMemoryService(repo)

    service.record_user_utterance(
        user_id="alice",
        text="Frontend developers build interfaces.",
        session_id="s1",
    )

    results = service.graph_search(user_id="alice", query="build", limit=8)
    assert results
    assert all(set(item) <= {"focus", "relations", "source"} for item in results)
    assert all(len(item["relations"]) <= 6 for item in results)
    assert any(item.get("relations") for item in results)
    assert any(
        relation.get("relation") in {"user_mentions_concept", "user_adjacent_concept", "user_references_concept"}
        for item in results
        for relation in item.get("relations", [])
    )
    repo.close()


def test_graph_search_expands_an_exact_returned_relation_node_id() -> None:
    repo = GraphRepository(":memory:")
    service = GraphMemoryService(repo)
    service.record_user_utterance(
        user_id="alice",
        text="강지는 스텔라이브를 만들었다.",
        session_id="s1",
    )

    initial = service.graph_search(user_id="alice", query="강지는", limit=1)
    assert initial and initial[0]["relations"]
    relation_node_id = initial[0]["relations"][0]["node_id"]

    expanded = service.graph_search(user_id="alice", node_id=relation_node_id)

    assert len(expanded) == 1
    assert expanded[0]["focus"]["node_id"] == relation_node_id
    assert len(expanded[0]["relations"]) <= 6
    repo.close()


def test_graph_search_node_id_cannot_expand_another_users_private_node() -> None:
    repo = GraphRepository(":memory:")
    service = GraphMemoryService(repo)
    utterance_id = service.record_user_utterance(
        user_id="alice",
        text="Alice private memory.",
        session_id="s1",
    )

    assert service.graph_search(user_id="bob", node_id=utterance_id) == []
    repo.close()


def test_graph_repository_persists_across_reopen(tmp_path: Path) -> None:
    db_path = tmp_path / "MK5-memory.db"
    repo_a = GraphRepository(db_path)
    service_a = GraphMemoryService(repo_a)
    service_a.record_user_utterance(user_id="alice", text="persist me", session_id="s1")
    repo_a.close()

    repo_b = GraphRepository(db_path)
    service_b = GraphMemoryService(repo_b)
    summary = service_b.user_memory_summary("alice")

    assert any("persist me" in item["label"] for item in summary)
    repo_b.close()


def test_search_results_are_persisted_under_search_anchor() -> None:
    repo = GraphRepository(":memory:")
    service = GraphMemoryService(repo)

    recorded = service.record_search_results(
        query="graph memory",
        results=[
            {
                "title": "Graph Memory",
                "url": "https://example.com/graph-memory",
                "snippet": "Graph memory stores durable context for agents.",
                "source": "stub",
            }
        ],
    )

    assert recorded
    search_results = service.graph_search(user_id="alice", query="Graph Memory", limit=8)
    assert any(item["focus"]["node_type"] == "search_result" for item in search_results)
    durable_results = service.graph_search(user_id="alice", query="durable", limit=8)
    assert any(item["focus"]["node_type"] == "search_fact" for item in durable_results)
    concept_results = service.graph_search(user_id="alice", query="agents", limit=8)
    assert any(item["focus"]["node_type"] == "concept" for item in concept_results)
    repo.close()


def test_repeated_relation_reinforces_one_semantic_edge() -> None:
    repo = GraphRepository(":memory:")
    service = GraphMemoryService(repo)

    service.record_user_utterance(user_id="alice", text="I enjoy TypeScript.", session_id="s1")
    service.record_user_utterance(user_id="alice", text="I enjoy TypeScript.", session_id="s2")

    asserted = [edge for edge in repo.all_edges() if edge.relation == "asserted_fact"]
    assert len(asserted) == 1
    assert asserted[0].support_count >= 2
    repo.close()


def test_memory_summary_ranks_current_query_context() -> None:
    repo = GraphRepository(":memory:")
    service = GraphMemoryService(repo)
    service.record_user_utterance(user_id="alice", text="I enjoy TypeScript.", session_id="s1")
    service.record_user_utterance(user_id="alice", text="I grow tomatoes.", session_id="s1")

    summary = service.user_memory_summary("alice", query="TypeScript project", limit=1)

    assert len(summary) == 1
    assert "I enjoy TypeScript." in summary[0]["label"]
    assert "사용자(alice)" in summary[0]["label"]
    repo.close()


def test_memory_summary_items_include_scores_and_components() -> None:
    repo = GraphRepository(":memory:")
    service = GraphMemoryService(repo)
    service.record_user_utterance(user_id="alice", text="I enjoy TypeScript.", session_id="s1")
    service.record_user_utterance(user_id="alice", text="I grow tomatoes.", session_id="s1")

    items = service.user_memory_summary("alice", query="TypeScript project", limit=1)

    assert len(items) == 1
    assert "I enjoy TypeScript." in items[0]["label"]
    assert isinstance(items[0]["score"], float)
    assert items[0]["score_components"]["relevance"] > 0
    assert "activation_bonus" in items[0]["score_components"]
    assert "trust_score" in items[0]["score_components"]
    repo.close()


def test_memory_summary_min_signal_excludes_unrelated_stable_memories() -> None:
    repo = GraphRepository(":memory:")
    service = GraphMemoryService(repo)
    service.record_user_utterance(user_id="alice", text="I enjoy TypeScript.", session_id="s1")
    service.record_user_utterance(user_id="alice", text="I grow tomatoes.", session_id="s1")

    items = service.user_memory_summary(
        "alice",
        query="TypeScript project",
        limit=5,
        min_signal=0.05,
    )

    assert items
    assert all("tomatoes" not in item["raw_label"] for item in items)
    assert any("TypeScript" in item["raw_label"] for item in items)
    repo.close()


def test_memory_summary_deduplicates_fact_and_utterance_with_same_text() -> None:
    repo = GraphRepository(":memory:")
    service = GraphMemoryService(repo)
    service.record_user_utterance(user_id="alice", text="I enjoy TypeScript.", session_id="s1")

    items = service.user_memory_summary(
        "alice",
        query="TypeScript",
        limit=5,
        min_signal=0.05,
    )

    assert [item["raw_label"] for item in items] == ["I enjoy TypeScript."]
    repo.close()


def test_memory_summary_min_signal_does_not_force_fill_requested_limit() -> None:
    repo = GraphRepository(":memory:")
    service = GraphMemoryService(repo)
    for index in range(8):
        service.record_user_utterance(
            user_id="alice",
            text=f"unrelated memory number {index}",
            session_id="s1",
        )
    service.record_user_utterance(user_id="alice", text="Machi uses graph memory.", session_id="s1")

    items = service.user_memory_summary(
        "alice",
        query="Machi graph",
        limit=5,
        min_signal=0.05,
    )

    assert 1 <= len(items) < 5
    assert all("Machi" in item["raw_label"] or "graph" in item["raw_label"] for item in items)
    repo.close()


def test_memory_summary_limit_zero_returns_all_ranked_items() -> None:
    repo = GraphRepository(":memory:")
    service = GraphMemoryService(repo)
    service.record_user_utterance(user_id="alice", text="첫 번째 기억", session_id="s1")
    service.record_user_utterance(user_id="alice", text="두 번째 기억", session_id="s1")
    service.record_user_utterance(user_id="alice", text="세 번째 기억", session_id="s1")

    limited = service.user_memory_summary("alice", limit=1)
    unlimited = service.user_memory_summary("alice", limit=0)

    assert len(limited) == 1
    assert len(unlimited) > len(limited)
    assert any("첫 번째 기억" in item["label"] for item in unlimited)
    assert any("두 번째 기억" in item["label"] for item in unlimited)
    assert any("세 번째 기억" in item["label"] for item in unlimited)
    repo.close()


def test_memory_summary_preserves_user_speaker_attribution() -> None:
    repo = GraphRepository(":memory:")
    service = GraphMemoryService(repo)
    service.record_user_utterance(user_id="신재용", text="나는 신재용이야.", session_id="s1")

    summary = service.user_memory_summary("신재용", query="나에 대해 기억하니", limit=3)

    assert summary
    assert any("사용자(신재용)" in item["label"] for item in summary)
    assert any("assistant가 아니라 사용자" in item["label"] or "speaker는 사용자" in item["label"] for item in summary)
    repo.close()


def test_fact_correction_preserves_history_and_hides_superseded_fact() -> None:
    repo = GraphRepository(":memory:")
    service = GraphMemoryService(repo)
    service.record_user_utterance(user_id="alice", text="I use JavaScript.", session_id="s1")
    previous = next(node for node in repo.all_nodes() if node.node_type == "fact")

    replacement_id = service.record_fact_correction(
        user_id="alice",
        previous_fact_id=previous.node_id,
        replacement_text="I use TypeScript.",
        session_id="s2",
    )

    old = repo.get_node(previous.node_id)
    replacement = repo.get_node(replacement_id)
    assert old is not None and old.is_active is False
    assert old.payload["superseded_by"] == replacement_id
    assert replacement is not None and replacement.provenance == "user_correction"
    corrected_summary = service.user_memory_summary("alice")
    assert not any("I use JavaScript." in item["label"] for item in corrected_summary)
    assert any("I use TypeScript." in item["label"] for item in corrected_summary)
    assert any(edge.relation == "replaces" for edge in repo.all_edges())
    repo.close()


def test_user_scoped_search_does_not_return_other_users_fact() -> None:
    repo = GraphRepository(":memory:")
    service = GraphMemoryService(repo)
    service.record_user_utterance(user_id="alice", text="Alice secret preference.", session_id="s1")

    results = service.graph_search(user_id="bob", query="secret preference", limit=8)

    assert not any(item["focus"]["node_type"] == "fact" for item in results)
    assert not any(item["focus"]["node_type"] == "utterance" for item in results)
    repo.close()


def test_record_file_text_activation_keeps_ranked_text_nodes() -> None:
    repo = GraphRepository(":memory:")
    service = GraphMemoryService(repo)

    result = service.record_file_text_activation(
        user_id="alice",
        path="note.md",
        content="Machi\n\n감성 샤워 감성 몽환 프로젝트 기억 그래프 도구",
        session_id="s1",
    )

    assert result["context_node_id"].startswith("file_context::")
    assert result["node_ids"]
    labels = [item["label"] for item in result["nodes"]]
    assert "감성" in labels
    assert "샤워" in labels
    context = repo.get_node(result["context_node_id"])
    assert context is not None
    assert context.node_type == "file_context"
    assert context.provenance == "file_read"
    assert context.payload["suppress_from_summary"] is True
    assert not service.user_memory_summary("alice", query="감성 샤워", limit=0)
    repo.close()


def test_record_file_text_activation_ignores_non_text_extensions() -> None:
    repo = GraphRepository(":memory:")
    service = GraphMemoryService(repo)

    result = service.record_file_text_activation(
        user_id="alice",
        path="image.jpg",
        content="감성 샤워",
        session_id="s1",
    )

    assert result == {"node_ids": [], "nodes": []}
    repo.close()

