from __future__ import annotations

from mai.context import compact_tool_event


def test_file_read_compaction_preserves_structure_but_limits_content() -> None:
    original = {
        "tool": "file_read",
        "arguments": {"path": "C:/repo/a.py"},
        "result": {
            "path": "C:/repo/a.py",
            "start_line": 1,
            "end_line": 1000,
            "total_lines": 1000,
            "content": "line\n" * 10000,
        },
    }

    compact = compact_tool_event(original)

    assert compact["tool"] == "file_read"
    assert compact["arguments"]["path"] == "C:/repo/a.py"
    assert compact["result"]["path"] == "C:/repo/a.py"
    assert compact["result"]["total_lines"] == 1000
    assert len(compact["result"]["content"]) <= 2450
    assert original["result"]["content"] == "line\n" * 10000


def test_terminal_compaction_uses_output_tails() -> None:
    compact = compact_tool_event(
        {
            "tool": "terminal_command",
            "arguments": {"command": "example"},
            "result": {
                "returncode": 0,
                "stdout": "a" * 1000 + "END",
                "stderr": "b" * 1000 + "ERR",
            },
        }
    )

    assert compact["result"]["returncode"] == 0
    assert compact["result"]["stdout_tail"].endswith("END")
    assert compact["result"]["stderr_tail"].endswith("ERR")
    assert len(compact["result"]["stdout_tail"]) <= 500
    assert len(compact["result"]["stderr_tail"]) <= 500
