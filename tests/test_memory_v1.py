from datetime import datetime, timezone

from mai.memory.graph.repository import MemoryGraphRepository
from mai.memory.working import WorkingGraph

NOW = datetime(2026, 8, 27, 15, 24, tzinfo=timezone.utc)


def test_node_identity_is_exact_canonical_text(tmp_path):
    with MemoryGraphRepository(tmp_path / "memory.db") as repo:
        first, created_first = repo.get_or_create_node("Ornith", now=NOW)
        second, created_second = repo.get_or_create_node("Ornith", now=NOW)
        other, _ = repo.get_or_create_node("ornith", now=NOW)

        assert created_first is True
        assert created_second is False
        assert first.id == second.id
        assert second.occurrence_count == 2
        assert other.id != first.id


def test_directed_edge_is_unique_and_keeps_latest_three_observations(tmp_path):
    with MemoryGraphRepository(tmp_path / "memory.db") as repo:
        a, _ = repo.get_or_create_node("A", now=NOW)
        b, _ = repo.get_or_create_node("B", now=NOW)
        evidence_ids = [repo.record_evidence("user_utterance", f"evidence {i}", now=NOW).id for i in range(4)]

        for i, evidence_id in enumerate(evidence_ids):
            edge = repo.observe_relation(a.id, b.id, f"relation {i}", evidence_id=evidence_id, now=NOW)

        assert [item.detail for item in edge.observations] == ["relation 3", "relation 2", "relation 1"]
        count = repo.connection.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        evidence_count = repo.connection.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]
        assert count == 1
        assert evidence_count == 4

        reverse = repo.observe_relation(b.id, a.id, "reverse", evidence_id=evidence_ids[-1], now=NOW)
        assert reverse.id != edge.id
        assert repo.connection.execute("SELECT COUNT(*) FROM edges").fetchone()[0] == 2


def test_working_graph_merges_one_hop_without_mutating_permanent_graph(tmp_path):
    with MemoryGraphRepository(tmp_path / "memory.db") as repo:
        a, _ = repo.get_or_create_node("A", now=NOW)
        b, _ = repo.get_or_create_node("B", now=NOW)
        evidence = repo.record_evidence("user_utterance", "A relates to B", now=NOW)
        repo.observe_relation(a.id, b.id, "related", evidence_id=evidence.id, now=NOW)

        working = WorkingGraph()
        working.merge(repo.one_hop(a.id))
        assert set(working.nodes) == {a.id, b.id}
        assert working.expanded_node_ids == {a.id}
        assert repo.connection.execute("SELECT COUNT(*) FROM edges").fetchone()[0] == 1
