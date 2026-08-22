from __future__ import annotations

from pathlib import Path

from mai.web import ChatHistoryStore


def test_raw_chat_history_stays_separate_from_compact_tool_operations(tmp_path: Path) -> None:
    store = ChatHistoryStore(tmp_path / "chat.db")
    try:
        store.append_turn(
            user_id="owner",
            turn_id="t1",
            user_text="내가 방금 한 말 그대로",
            assistant_text="응답 원문 그대로",
        )
        store.append_tool_operations(
            user_id="owner",
            turn_id="t1",
            events=[
                {
                    "tool": "file_read",
                    "arguments": {"path": "/tmp/large.txt"},
                    "result": {
                        "path": "/tmp/large.txt",
                        "content": "x" * 10000,
                        "total_lines": 1000,
                    },
                }
            ],
        )

        messages = store.list_messages(user_id="owner", limit=10)
        operations = store.list_tool_operations(user_id="owner", limit=5)

        assert [(item["role"], item["content"]) for item in messages] == [
            ("user", "내가 방금 한 말 그대로"),
            ("assistant", "응답 원문 그대로"),
        ]
        assert len(operations) == 1
        assert operations[0]["tool"] == "file_read"
        assert len(operations[0]["result"]["content"]) < 4000
        assert "...[truncated]..." in operations[0]["result"]["content"]
    finally:
        store.close()


def test_recent_tool_operation_limit_returns_latest_entries_in_order(tmp_path: Path) -> None:
    store = ChatHistoryStore(tmp_path / "chat.db")
    try:
        store.append_tool_operations(
            user_id="owner",
            turn_id="t1",
            events=[
                {"tool": "file_search", "arguments": {"pattern": str(index)}, "result": {"count": index}}
                for index in range(7)
            ],
        )

        operations = store.list_tool_operations(user_id="owner", limit=5)
        assert [item["arguments"]["pattern"] for item in operations] == ["2", "3", "4", "5", "6"]
    finally:
        store.close()
