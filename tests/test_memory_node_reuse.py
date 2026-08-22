from __future__ import annotations

from mai.graph import GraphRepository
from mai.memory_write import MemoryTurnScope, WriteMemoryTool


def scope(*, turn_id: str) -> MemoryTurnScope:
    return MemoryTurnScope(
        user_id="owner",
        turn_id=turn_id,
        user_text="신재용",
        assistant_text="기억함",
        recalled_node_ids=frozenset(),
    )


def test_write_memory_reuses_exact_existing_nodes_and_reinforces_edge(tmp_path) -> None:
    repository = GraphRepository(tmp_path / "graph.sqlite3")
    try:
        repository.ensure_user_anchor(user_id="owner", turn_id="seed", source_text="seed")
        writer = WriteMemoryTool(repository)
        arguments = {
            "subject": {"new_node": {"name": "신재용"}},
            "relation": "이름",
            "object": {"new_node": {"name": "재용"}},
        }

        first = writer.execute(arguments=arguments, scope=scope(turn_id="t1"))
        second = writer.execute(arguments=arguments, scope=scope(turn_id="t2"))

        assert len(first["created_nodes"]) == 2
        assert second["created_nodes"] == []
        assert first["edge"]["edge_id"] == second["edge"]["edge_id"]
        assert second["edge"]["support_count"] == 2

        reused = repository.lookup_nodes(user_id="owner", queries=["신재용"])["matches"]
        assert [node["name"] for node in reused].count("신재용") == 1
    finally:
        repository.close()


def test_node_reuse_is_scoped_per_user(tmp_path) -> None:
    repository = GraphRepository(tmp_path / "graph.sqlite3")
    try:
        for user_id in ("owner", "member"):
            repository.ensure_user_anchor(user_id=user_id, turn_id=f"seed-{user_id}", source_text="seed")
        owner_writer = WriteMemoryTool(repository)
        owner_writer.execute(
            arguments={
                "subject": {"kind": "user"},
                "relation": "이름",
                "object": {"new_node": {"name": "신재용"}},
            },
            scope=scope(turn_id="owner-turn"),
        )
        member_scope = MemoryTurnScope(
            user_id="member",
            turn_id="member-turn",
            user_text="신재용",
            assistant_text="기억함",
            recalled_node_ids=frozenset(),
        )
        member_result = owner_writer.execute(
            arguments={
                "subject": {"kind": "user"},
                "relation": "이름",
                "object": {"new_node": {"name": "신재용"}},
            },
            scope=member_scope,
        )
        assert len(member_result["created_nodes"]) == 1
    finally:
        repository.close()
