from mai.memory.working import WorkingGraph
from mai.memory.graph.models import MemoryNode, MemoryEdge


def test_model_snapshot_keeps_content_and_excludes_graph_metadata():
    graph = WorkingGraph()
    graph.nodes[1] = MemoryNode(1, "anchor", "anchor", "user", {}, 1, "2026-01-01", "2026-01-01")
    graph.nodes[2] = MemoryNode(2, "concept", "concept", "M", {}, 9, "2026-01-01", "2026-01-01")
    graph.nodes[3] = MemoryNode(3, "fact", "fact", "project location", {}, 1, "2026-01-02", "2026-01-02")
    graph.nodes[4] = MemoryNode(4, "utterance", "utterance", "original text", {"evidence_id": 8}, 1, "2026-01-03", "2026-01-03")
    graph.edges[10] = MemoryEdge(10, 1, 3, "asserted_fact", "user_assertion", "2026-01-02")
    graph.edges[11] = MemoryEdge(11, 3, 2, "mentions", "fact_index", "2026-01-02")
    graph.edges[12] = MemoryEdge(12, 1, 4, "spoke", "user_utterance", "2026-01-03")
    result = graph.model_snapshot()
    assert list(result) == ["nodes"]
    assert [node["node_id"] for node in result["nodes"]] == [3, 4]
    assert all(set(node) == {"node_id", "node_type", "data", "created_at", "provenance", "evidence_id"} for node in result["nodes"])
    assert result["nodes"][0]["provenance"] == ["user_assertion"]
    assert result["nodes"][1]["evidence_id"] == 8
    assert len(graph.edges) == 3
    assert len(graph.nodes) == 4
