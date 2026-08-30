from dataclasses import dataclass

from mai.memory.admission import (
    should_skip_recall_without_new_facts,
    successful_memory_recall_tools,
    successful_non_recall_tool_results,
    successful_tool_names,
)


@dataclass(frozen=True)
class Execution:
    name: str
    ok: bool
    content: str = ""

    @property
    def context_content(self) -> str:
        return self.content


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


def test_pure_recall_with_no_new_facts_is_skipped() -> None:
    executions = (
        Execution("memory_overview", True, "old memories"),
        Execution("memory_search", True, "expanded old memories"),
    )
    assert should_skip_recall_without_new_facts(
        executions,
        extracted_facts=(),
        extraction_succeeded=True,
    ) is True


def test_recall_only_tool_usage_does_not_drop_new_user_fact() -> None:
    executions = (Execution("memory_recall", True, "old memory"),)
    assert should_skip_recall_without_new_facts(
        executions,
        extracted_facts=("사용자는 최근 목표를 Y로 변경했다",),
        extraction_succeeded=True,
    ) is False


def test_recall_plus_non_recall_tool_can_store_extracted_fact() -> None:
    executions = (
        Execution("memory_recall", True, "old memory"),
        Execution("document_read", True, "new document evidence"),
    )
    assert should_skip_recall_without_new_facts(
        executions,
        extracted_facts=("문서에서 사용자의 새 계획이 확인됐다",),
        extraction_succeeded=True,
    ) is False


def test_extraction_failure_fails_safe_and_preserves_recall_turn() -> None:
    executions = (Execution("memory_recall", True, "old memory"),)
    assert should_skip_recall_without_new_facts(
        executions,
        extracted_facts=(),
        extraction_succeeded=False,
    ) is False


def test_non_recall_turn_is_not_suppressed_even_without_facts() -> None:
    executions = (
        Execution("file_search", True, "files"),
        Execution("calculator", True, "42"),
    )
    assert should_skip_recall_without_new_facts(
        executions,
        extracted_facts=(),
        extraction_succeeded=True,
    ) is False
