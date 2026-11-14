from datetime import datetime, timezone

from mai.memory.graph.repository import MemoryGraphRepository
from mai.memory.working import WorkingGraph

NOW = datetime(2026, 8, 27, 15, 24, tzinfo=timezone.utc)


def test_user_anchor_is_persistent_per_account(tmp_path):
    with MemoryGraphRepository(tmp_path / "memory.db") as repo:
        first = repo.ensure_user_anchor("alice", now=NOW)
        second = repo.ensure_user_anchor("alice", now=NOW)
        other = repo.ensure_user_anchor("bob", now=NOW)

        assert first.id == second.id
        assert first.node_type == "anchor"
        assert first.payload["user_id"] == "alice"
        assert other.id != first.id


def test_concept_identity_is_exact_segment_text(tmp_path):
    with MemoryGraphRepository(tmp_path / "memory.db") as repo:
        first, created_first = repo.get_or_create_concept("Ornith", now=NOW)
        second, created_second = repo.get_or_create_concept("Ornith", now=NOW)
        other, _ = repo.get_or_create_concept("ornith", now=NOW)

        assert created_first is True
        assert created_second is False
        assert first.id == second.id
        assert second.occurrence_count == 2
        assert other.id != first.id


def test_typed_graph_keeps_original_utterance_as_direct_evidence(tmp_path):
    with MemoryGraphRepository(tmp_path / "memory.db") as repo:
        anchor = repo.ensure_user_anchor("alice", now=NOW)
        evidence = repo.record_evidence("user_utterance", "나는 MAI를 개인 AI 프로젝트로 만들고 있어.", now=NOW)
        utterance = repo.create_utterance_node(user_id="alice", evidence=evidence, now=NOW)
        fact, _ = repo.get_or_create_fact(
            user_id="alice",
            text="MAI는 사용자의 개인 AI 프로젝트다.",
            now=NOW,
        )
        concept, _ = repo.get_or_create_concept("MAI", now=NOW)

        repo.add_typed_edge(anchor.id, utterance.id, "spoke", provenance="user_utterance", now=NOW)
        repo.add_typed_edge(utterance.id, fact.id, "derived_fact", provenance="derived_from_utterance", now=NOW)
        repo.add_typed_edge(utterance.id, concept.id, "mentions", provenance="user_utterance", now=NOW)
        repo.add_typed_edge(fact.id, concept.id, "mentions", provenance="fact_index", now=NOW)

        neighborhood = repo.one_hop(concept.id)
        utterance_nodes = [node for node in neighborhood.nodes if node.node_type == "utterance"]
        assert len(utterance_nodes) == 1
        assert utterance_nodes[0].canonical_text == evidence.content
        assert {edge.relation for edge in neighborhood.edges} == {"mentions"}

        path = repo.shortest_path_to_user_anchor(concept.id, "alice")
        assert path is not None
        assert path.nodes[-1].id == anchor.id
        assert any(node.node_type == "utterance" for node in path.nodes)


def test_same_pair_may_have_distinct_typed_relations_but_not_duplicates(tmp_path):
    with MemoryGraphRepository(tmp_path / "memory.db") as repo:
        anchor = repo.ensure_user_anchor("alice", now=NOW)
        evidence = repo.record_evidence("user_utterance", "hello", now=NOW)
        utterance = repo.create_utterance_node(user_id="alice", evidence=evidence, now=NOW)

        first = repo.add_typed_edge(anchor.id, utterance.id, "spoke", provenance="user_utterance", now=NOW)
        duplicate = repo.add_typed_edge(anchor.id, utterance.id, "spoke", provenance="other", now=NOW)
        received = repo.add_typed_edge(anchor.id, utterance.id, "received", provenance="test", now=NOW)

        assert first.id == duplicate.id
        assert received.id != first.id
        assert repo.connection.execute("SELECT COUNT(*) FROM edges").fetchone()[0] == 2


def test_working_graph_merges_typed_one_hop_without_mutating_permanent_graph(tmp_path):
    with MemoryGraphRepository(tmp_path / "memory.db") as repo:
        a, _ = repo.get_or_create_concept("A", now=NOW)
        b, _ = repo.get_or_create_concept("B", now=NOW)
        repo.add_typed_edge(a.id, b.id, "mentions", provenance="test", now=NOW)

        working = WorkingGraph()
        working.merge(repo.one_hop(a.id))
        assert set(working.nodes) == {a.id, b.id}
        assert working.expanded_node_ids == {a.id}
        assert next(iter(working.edges.values())).relation == "mentions"
        assert repo.connection.execute("SELECT COUNT(*) FROM edges").fetchone()[0] == 1
