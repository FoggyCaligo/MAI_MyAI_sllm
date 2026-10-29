"""Optional memory recall limit and unmodified verifier tool evidence."""
from types import SimpleNamespace

import pytest

from mai.memory.recall.service import RecallService
from mai.memory.graph.models import GraphNeighborhood
from mai.memory.index.base import ConceptHit
from mai.agent.verification import _review_payload


class FakeIndex:
    def search(self, queries, *, limit):
        return {
            "alpha": (ConceptHit(10, 1.0, "exact"),),
            "beta": (ConceptHit(20, 0.5, "fts5"),),
        }[queries[0]]


class FakeGraph:
    def __init__(self):
        self.nodes = {
            1: SimpleNamespace(id=1, node_type="anchor", payload={}),
            10: SimpleNamespace(id=10, node_type="concept", payload={}),
            20: SimpleNamespace(id=20, node_type="concept", payload={}),
            101: SimpleNamespace(id=101, node_type="fact", payload={"user_id": "u"}),
            102: SimpleNamespace(id=102, node_type="fact", payload={"user_id": "u"}),
            103: SimpleNamespace(id=103, node_type="fact", payload={"user_id": "u"}),
        }

    def get_user_anchor(self, user_id):
        return self.nodes[1]

    def one_hop(self, node_id):
        ids = (10, 101, 102) if node_id == 10 else (20, 102, 103)
        return GraphNeighborhood(node_id, tuple(self.nodes[i] for i in ids), ())

    def shortest_path_to_user_anchor(self, node_id, user_id):
        return GraphNeighborhood(node_id, (self.nodes[1], self.nodes[node_id]), ())


def test_recall_limit_selects_top_ranked_facts_and_unlimited_preserves_all():
    recall = RecallService(FakeGraph(), FakeIndex())
    limited = recall.recall_query(user_id="u", query="alpha beta", limit=2)
    assert {i for i in limited.nodes if i != 1} == {101, 102}
    unlimited = recall.recall_query(user_id="u", query="alpha beta")
    assert {i for i in unlimited.nodes if i != 1} == {101, 102, 103}


def test_recall_limit_invalid_fails():
    recall = RecallService(FakeGraph(), FakeIndex())
    with pytest.raises(ValueError, match="limit"):
        recall.recall_query(user_id="u", query="alpha", limit=0)


def test_verifier_does_not_truncate_selected_tool_evidence():
    long_result = "x" * 12000
    payload = _review_payload(
        candidate="Response", messages=[{"role": "user", "content": "Question"}],
        tool_results=[("memory_recall", True, None, long_result)],
    )
    assert payload["tool_results_in_execution_order"][0]["result"] == long_result


def test_recall_tool_schema_exposes_optional_limit_to_model():
    from mai.memory.tools import MemorySearchInput
    schema = MemorySearchInput.model_json_schema()
    assert "limit" in schema["properties"]
    assert "limit" not in schema.get("required", [])
    assert "keyword recall" in schema["properties"]["limit"]["description"]
