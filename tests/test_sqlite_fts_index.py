from datetime import datetime, timezone

from mai.memory.graph.repository import MemoryGraphRepository
from mai.memory.index import SqliteFtsConceptIndex

NOW = datetime(2026, 8, 28, tzinfo=timezone.utc)


def test_exact_lookup_precedes_fts5(tmp_path):
    path = tmp_path / "memory.db"
    index = SqliteFtsConceptIndex(path)
    try:
        index.add_node(1, "고양이 이름")
        index.add_node(2, "고양이 장난감")
        hits = index.search(("고양이 이름",), limit=5)
        assert hits[0].node_id == 1
        assert hits[0].match_kind == "exact"
    finally:
        index.close()


def test_fts5_returns_lexical_concept_without_embedding(tmp_path):
    path = tmp_path / "memory.db"
    index = SqliteFtsConceptIndex(path)
    try:
        index.add_node(1, "고양이 이름")
        index.add_node(2, "강아지 산책")
        hits = index.search(("고양이",), limit=5)
        assert [hit.node_id for hit in hits] == [1]
        assert hits[0].match_kind == "fts5"
    finally:
        index.close()


def test_existing_graph_concepts_are_synced_non_destructively(tmp_path):
    path = tmp_path / "memory.db"
    graph = MemoryGraphRepository(path)
    try:
        concept, created = graph.get_or_create_concept("MAI 프로젝트", now=NOW)
        assert created is True
    finally:
        graph.close()

    index = SqliteFtsConceptIndex(path)
    try:
        hits = index.search(("MAI 프로젝트",), limit=5)
        assert hits[0].node_id == concept.id
        assert hits[0].match_kind == "exact"
    finally:
        index.close()


def test_conflicting_duplicate_fails_visibly(tmp_path):
    index = SqliteFtsConceptIndex(tmp_path / "memory.db")
    try:
        index.add_node(1, "MAI")
        try:
            index.add_node(2, "MAI")
        except ValueError as exc:
            assert "already indexed" in str(exc)
        else:
            raise AssertionError("conflicting duplicate must fail")
    finally:
        index.close()
