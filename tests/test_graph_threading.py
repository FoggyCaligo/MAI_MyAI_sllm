from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from mai.graph import GraphRepository


def test_graph_repository_connection_can_be_used_from_worker_thread(tmp_path) -> None:
    repository = GraphRepository(tmp_path / "graph.sqlite3")
    try:
        def ensure_anchor() -> dict:
            return repository.ensure_user_anchor(user_id="owner")

        with ThreadPoolExecutor(max_workers=1) as executor:
            anchor = executor.submit(ensure_anchor).result(timeout=5)

        assert anchor["user_id"] == "owner"
    finally:
        repository.close()


def test_graph_repository_serializes_parallel_writes(tmp_path) -> None:
    repository = GraphRepository(tmp_path / "graph.sqlite3")
    try:
        def create(index: int) -> dict:
            return repository.create_node(user_id="owner", name=f"node-{index}")

        with ThreadPoolExecutor(max_workers=4) as executor:
            nodes = list(executor.map(create, range(8)))

        assert sorted(node["name"] for node in nodes) == [f"node-{index}" for index in range(8)]
    finally:
        repository.close()


def test_current_graph_schema_can_be_reopened(tmp_path) -> None:
    path = tmp_path / "graph.sqlite3"
    first = GraphRepository(path)
    try:
        first.ensure_user_anchor(user_id="owner")
    finally:
        first.close()

    second = GraphRepository(path)
    try:
        assert second.get_user_anchor(user_id="owner")["name"] == "사용자"
    finally:
        second.close()


def test_retired_graph_schema_fails_visibly_and_requires_reset(tmp_path) -> None:
    path = tmp_path / "graph.sqlite3"
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            CREATE TABLE graph_nodes (
                node_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL
            )
            """
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(RuntimeError, match="retired schema"):
        GraphRepository(path)
