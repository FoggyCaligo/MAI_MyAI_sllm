from dataclasses import dataclass

from mai.memory.admission import (
    is_recall_only_turn,
    successful_memory_recall_tools,
    successful_non_recall_tool_results,
    successful_tool_names,
)


@dataclass(frozen=True)
class Execution:
    name: str
    ok: bool
    content: str = ""


def test_successful_memory_recall_tools_detects_persistent_memory_reads() -> None:
    executions = (
        Execution("memory_overview", True),
        Execution("memory_search", True),
        Execution("file_search", True),
        Execution("memory_recall", False),
    )

    assert successful_memory_recall_tools(executions) == (
        "memory_overview",
        "memory_search",
    )
    assert successful_tool_names(executions) == (
        "file_search",
        "memory_overview",
        "memory_search",
    )


def test_recall_only_turn_requires_all_successful_tools_to_be_memory_reads() -> None:
    assert is_recall_only_turn((
        Execution("memory_overview", True),
        Execution("memory_search", True),
    )) is True

    assert is_recall_only_turn((
        Execution("memory_overview", True),
        Execution("file_search", True),
    )) is False

    assert is_recall_only_turn((
        Execution("memory_overview", False),
        Execution("file_search", True),
    )) is False

    assert is_recall_only_turn(()) is False


def test_successful_non_recall_tool_results_excludes_recall_results() -> None:
    executions = (
        Execution("memory_recall", True, "old persistent memory"),
        Execution("document_read", True, "new document evidence"),
        Execution("web_search", True, "new web evidence"),
        Execution("calculator", False, "failed calculation"),
    )

    assert successful_non_recall_tool_results(executions) == (
        "new document evidence",
        "new web evidence",
    )


def test_non_memory_tool_turns_remain_eligible_for_storage() -> None:
    executions = (
        Execution("file_search", True, "files"),
        Execution("calculator", True, "42"),
    )
    assert successful_memory_recall_tools(executions) == ()
    assert is_recall_only_turn(executions) is False
