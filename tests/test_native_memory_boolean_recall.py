"""Native memory_search retains the old tool with MCP_memorizer recall enhancements."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from mai.memory.graph.repository import MemoryGraphRepository
from mai.memory.index import SqliteFtsConceptIndex
from mai.memory.recall.service import RecallService
from mai.memory.runtime import MemoryRuntime
from mai.memory.tools import MemorySearchInput, register_memory_tools
from mai.memory.working import WorkingGraph
from mai.tools.registry import ToolRegistry

NOW = datetime(2026, 11, 14, tzinfo=timezone.utc)


class Words:
    def segment(self, text: str):
        return tuple(text.split())


def test_native_boolean_recall_preserves_fact_utterance_and_user_boundary(tmp_path):
    path = tmp_path / "memory.sqlite3"
    graph = MemoryGraphRepository(path)
    index = SqliteFtsConceptIndex(path)
    anchor = graph.ensure_user_anchor("alice", now=NOW)

    def concept(name):
        node, created = graph.get_or_create_concept(name, now=NOW)
        if created:
            index.add_node(node.id, name)
        return node

    def fact(user, text, terms):
        home = graph.ensure_user_anchor(user, now=NOW)
        node, _ = graph.get_or_create_fact(user_id=user, text=text, now=NOW)
        graph.add_typed_edge(home.id, node.id, "asserted_fact", provenance="test", now=NOW)
        for term in terms:
            graph.add_typed_edge(node.id, concept(term).id, "mentions", provenance="test", now=NOW)
        return node

    try:
        only_a = fact("alice", "alpha preference", ("alpha",))
        fact("alice", "alpha beta cancelled", ("alpha", "beta"))
        only_b = fact("alice", "beta history", ("beta",))
        fact("bob", "alpha secret", ("alpha",))
        evidence = graph.record_evidence("user_utterance", "gamma update", now=NOW)
        utterance = graph.create_utterance_node(user_id="alice", evidence=evidence, now=NOW)
        graph.add_typed_edge(anchor.id, utterance.id, "spoke", provenance="test", now=NOW)
        graph.add_typed_edge(utterance.id, concept("gamma").id, "mentions", provenance="test", now=NOW)

        recall = RecallService(graph, index)
        selected = recall.recall_query(user_id="alice", query="alpha AND NOT beta")
        assert only_a.id in selected.nodes
        assert only_b.id not in selected.nodes
        assert all(node.payload.get("user_id", "alice") == "alice"
                   for node in selected.nodes.values())
        union = recall.recall_query(user_id="alice", query="alpha OR beta")
        assert only_a.id in union.nodes and only_b.id in union.nodes
        raw = recall.recall_query(user_id="alice", query="gamma")
        assert utterance.id in raw.nodes

        memory = MemoryRuntime(graph, index, Words(), recall, now=lambda: NOW)
        registry = ToolRegistry()
        working = WorkingGraph()
        register_memory_tools(registry, memory, working, user_id="alice")
        assert registry.names() == ("memory_search",)
        assert not registry.has("memorizer__memory_search")
        query = asyncio.run(registry.get("memory_search").invoke({
            "query": "alpha AND NOT beta", "limit": 10,
        }))
        assert any(x["node_id"] == only_a.id for x in query["nodes"])
        keyword = asyncio.run(registry.get("memory_search").invoke({"keyword": "gamma"}))
        assert any(x["node_id"] == utterance.id for x in keyword["nodes"])
        assert MemorySearchInput.model_validate({"keyword": "alpha"}).keyword == "alpha"
    finally:
        index.close()
        graph.close()
