from MK5.core.agent.orchestrator import _final_answer_evidence_guard_result
from MK5.tools.llm_client import ModelTurn


def test_memory_recall_requires_declared_graph_search_evidence() -> None:
    turn = ModelTurn(
        final_answer="과거 대화의 상세 내용입니다.",
        final_answer_kind="tool_completion",
        completion_tools=["graph_search"],
    )

    guard = _final_answer_evidence_guard_result(
        turn=turn,
        tool_history=[],
        rejected_final_answer=turn.final_answer or "",
    )

    assert guard is not None
    assert guard["error"] == "completion_tool_not_run"
    assert guard["missing_tools"] == ["graph_search"]


def test_memory_recall_accepts_successful_graph_search_evidence() -> None:
    turn = ModelTurn(
        final_answer="과거 대화의 상세 내용입니다.",
        final_answer_kind="tool_completion",
        completion_tools=["graph_search"],
    )

    guard = _final_answer_evidence_guard_result(
        turn=turn,
        tool_history=[{
            "tool": "graph_search",
            "arguments": {"query": "past context"},
            "result": {"ok": True, "results": []},
        }],
        rejected_final_answer=turn.final_answer or "",
    )

    assert guard is None
