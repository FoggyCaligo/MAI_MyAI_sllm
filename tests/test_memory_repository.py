from __future__ import annotations

from mai.memory.repository import MemoryRepository


def test_memory_write_and_reinforcement_are_single_transaction(tmp_path) -> None:
    repo = MemoryRepository(tmp_path / "memory.db")
    try:
        first = repo.upsert_memory(
            user_id="owner",
            subject="user::owner",
            relation="likes",
            object_="robotics",
            source_text="I like robotics",
        )
        second = repo.upsert_memory(
            user_id="owner",
            subject="user::owner",
            relation="likes",
            object_="robotics",
            source_text="I still like robotics",
        )
        assert first["ok"] is True
        assert second["support_count"] == 2
        assert isinstance(second["db_elapsed_ms"], float)
        assert isinstance(second["transaction_elapsed_ms"], float)
    finally:
        repo.close()


def test_recall_is_user_scoped(tmp_path) -> None:
    repo = MemoryRepository(tmp_path / "memory.db")
    try:
        repo.upsert_memory(user_id="a", subject="user::a", relation="likes", object_="A", source_text="a")
        repo.upsert_memory(user_id="b", subject="user::b", relation="likes", object_="B", source_text="b")
        rows = repo.recent_memories(user_id="a")
        assert len(rows) == 1
        assert rows[0]["object"] == "A"
    finally:
        repo.close()
