from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from mai.graph import GraphRepository


def test_graph_repository_connection_can_be_used_from_worker_thread(tmp_path) -> None:
    repository = GraphRepository(tmp_path / "graph.sqlite3")
    try:
        def ensure_anchor() -> dict:
            return repository.ensure_user_anchor(
                user_id="owner",
                turn_id="turn-worker",
                source_text="worker-thread initialization",
            )

        with ThreadPoolExecutor(max_workers=1) as executor:
            anchor = executor.submit(ensure_anchor).result(timeout=5)

        assert anchor["user_id"] == "owner"
    finally:
        repository.close()


def test_graph_repository_serializes_parallel_writes(tmp_path) -> None:
    repository = GraphRepository(tmp_path / "graph.sqlite3")
    try:
        def create(index: int) -> dict:
            return repository.create_node(
                user_id="owner",
                name=f"node-{index}",
                turn_id=f"turn-{index}",
                source_role="user",
                source_text=f"source-{index}",
            )

        with ThreadPoolExecutor(max_workers=4) as executor:
            nodes = list(executor.map(create, range(8)))

        assert sorted(node["name"] for node in nodes) == [f"node-{index}" for index in range(8)]
    finally:
        repository.close()
