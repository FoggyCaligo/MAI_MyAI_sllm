"""Boolean search grammar and memory set filtering regressions."""
from datetime import datetime, timezone

import pytest

from mai.memory.graph.repository import MemoryGraphRepository
from mai.memory.index.sqlite_fts import SqliteFtsConceptIndex
from mai.memory.recall.query import parse_query
from mai.memory.recall.service import RecallService


def _ids(result):
    return {node["node_id"] for node in result.model_snapshot()["nodes"]}


def _store(tmp_path):
    now = datetime.now(timezone.utc)
    graph = MemoryGraphRepository(tmp_path / "memory.sqlite3")
    index = SqliteFtsConceptIndex(tmp_path / "memory.sqlite3")
    alice = graph.ensure_user_anchor("alice", now=now)
    bob = graph.ensure_user_anchor("bob", now=now)
    ids = {}
    examples = [
        ("a", "질환", ("질환",), "alice"),
        ("b", "어릴 적 질환", ("질환", "어릴 적"), "alice"),
        ("c", "어릴 적 감기", ("어릴 적", "감기"), "alice"),
        ("d", "감기", ("감기",), "alice"),
        ("other", "타인의 질환", ("질환",), "bob"),
    ]
    for key, text, terms, owner in examples:
        node, _ = graph.get_or_create_fact(user_id=owner, text=text, now=now)
        ids[key] = node.id
        anchor = alice if owner == "alice" else bob
        graph.add_typed_edge(anchor.id, node.id, "asserted_fact", provenance="user_assertion", now=now)
        for term in terms:
            concept, created = graph.get_or_create_concept(term, now=now)
            if created:
                index.add_node(concept.id, concept.canonical_text)
            graph.add_typed_edge(node.id, concept.id, "mentions", provenance="fact_index", now=now)
    return graph, index, RecallService(graph, index), ids


def test_boolean_operator_precedence_and_parentheses():
    expr = parse_query("A OR B AND NOT C")
    assert expr.op == "or"
    assert expr.right.op == "and"
    assert expr.right.right.op == "not"
    assert parse_query("(A | B) & C").op == "and"
    assert parse_query("'어릴 적' & \"질병\"").left.value == "어릴 적"
    assert parse_query("질환 어릴 적").value == "질환 어릴 적"


@pytest.mark.parametrize("text", [
    "", "A &", "A OR", "(A | B", "A ) B", "A B & C",
    "A && B", "\"\"", "NOT", "A | | B",
])
def test_boolean_rejects_invalid_syntax(text):
    with pytest.raises(ValueError):
        parse_query(text)


def test_boolean_combines_matching_memories_and_excludes_current_user_only(tmp_path):
    graph, index, recall, ids = _store(tmp_path)
    try:
        assert _ids(recall.recall_query(user_id="alice", query="질환")) == {ids["a"], ids["b"]}
        assert _ids(recall.recall_query(user_id="alice", query="질환 OR 감기")) == {
            ids["a"], ids["b"], ids["c"], ids["d"]
        }
        assert _ids(recall.recall_query(user_id="alice", query="질환 AND \"어릴 적\"")) == {ids["b"]}
        assert _ids(recall.recall_query(user_id="alice", query="질환 AND NOT \"어릴 적\"")) == {ids["a"]}
        assert _ids(recall.recall_query(user_id="alice", query="NOT 감기")) == {ids["a"], ids["b"]}
        assert _ids(recall.recall_query(user_id="alice", query="NOT (질환 | 감기)")) == set()
        assert ids["other"] not in _ids(recall.recall_query(user_id="alice", query="NOT 감기"))
        assert len(_ids(recall.recall_query(user_id="alice", query="질환 | 감기", limit=2))) == 2
    finally:
        index.close()
        graph.close()


def test_query_injection_does_not_bypass_ownership(tmp_path):
    graph, index, recall, ids = _store(tmp_path)
    try:
        assert ids["other"] not in _ids(recall.recall_query(user_id="alice", query="질환 | NOT nonexistent"))
    finally:
        index.close()
        graph.close()
