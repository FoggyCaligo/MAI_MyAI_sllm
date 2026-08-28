from dataclasses import dataclass

from mai.memory.admission import successful_memory_recall_tools


@dataclass(frozen=True)
class Execution:
    name: str
    ok: bool


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


def test_successful_memory_recall_tools_ignores_non_memory_tools() -> None:
    assert successful_memory_recall_tools((
        Execution("file_search", True),
        Execution("calculator", True),
    )) == ()
