from mai.memory.vector.sqlite_vec import SqliteVecIndex


class FakeEmbeddingProvider:
    def embed(self, texts):
        mapping = {
            "MAI": (1.0, 0.0, 0.0),
            "Machi": (0.8, 0.2, 0.0),
            "고양이": (0.0, 1.0, 0.0),
        }
        return tuple(mapping[text] for text in texts)


def test_sqlite_vec_stores_one_vector_per_graph_node_id(tmp_path):
    index = SqliteVecIndex(tmp_path / "memory.db", FakeEmbeddingProvider())
    try:
        index.add_node(10, "MAI")
        index.add_node(11, "Machi")

        rows = index.connection.execute(
            "SELECT rowid FROM memory_node_vectors ORDER BY rowid"
        ).fetchall()
        assert [int(row["rowid"]) for row in rows] == [10, 11]

        try:
            index.add_node(10, "MAI")
        except ValueError as exc:
            assert "already exists" in str(exc)
        else:
            raise AssertionError("duplicate graph node vector must fail")
    finally:
        index.close()


def test_sqlite_vec_returns_existing_graph_node_ids(tmp_path):
    index = SqliteVecIndex(tmp_path / "memory.db", FakeEmbeddingProvider())
    try:
        index.add_node(10, "MAI")
        index.add_node(11, "Machi")
        index.add_node(12, "고양이")

        hits = index.search(("MAI",), limit=2)
        assert hits
        assert hits[0].node_id == 10
        assert all(hit.node_id in {10, 11, 12} for hit in hits)
    finally:
        index.close()
