from __future__ import annotations

import sqlite3

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


def test_revise_memory_updates_only_owned_memory(tmp_path) -> None:
    repo = MemoryRepository(tmp_path / "memory.db")
    try:
        created = repo.upsert_memory(
            user_id="owner",
            subject="user::owner",
            relation="likes",
            object_="robotics",
            source_text="old",
        )
        result = repo.revise_memory(
            user_id="owner",
            memory_id=created["memory_id"],
            subject="user::owner",
            relation="likes",
            object_="automation",
            source_text="new",
        )
        assert result["ok"] is True
        assert result["previous"]["object"] == "robotics"
        rows = repo.recent_memories(user_id="owner")
        assert rows[0]["memory_id"] == created["memory_id"]
        assert rows[0]["object"] == "automation"
        assert rows[0]["support_count"] == 1
    finally:
        repo.close()


def test_revise_memory_rejects_foreign_memory_id(tmp_path) -> None:
    repo = MemoryRepository(tmp_path / "memory.db")
    try:
        created = repo.upsert_memory(
            user_id="other",
            subject="user::other",
            relation="likes",
            object_="robotics",
            source_text="other",
        )
        try:
            repo.revise_memory(
                user_id="owner",
                memory_id=created["memory_id"],
                subject="user::owner",
                relation="likes",
                object_="automation",
                source_text="new",
            )
        except ValueError as exc:
            assert "not owned by this user" in str(exc)
        else:
            raise AssertionError("foreign memory_id must be rejected")
    finally:
        repo.close()


def test_revise_memory_unique_conflict_is_visible(tmp_path) -> None:
    repo = MemoryRepository(tmp_path / "memory.db")
    try:
        first = repo.upsert_memory(
            user_id="owner", subject="user::owner", relation="likes", object_="A", source_text="a"
        )
        repo.upsert_memory(
            user_id="owner", subject="user::owner", relation="likes", object_="B", source_text="b"
        )
        try:
            repo.revise_memory(
                user_id="owner",
                memory_id=first["memory_id"],
                subject="user::owner",
                relation="likes",
                object_="B",
                source_text="conflict",
            )
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("revision collision must stay visible")
    finally:
        repo.close()
