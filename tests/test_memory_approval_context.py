from mai.app.runtime import _memory_grounded_final_claims
from mai.memory.extraction.service import GroundedFinalClaimEvidence, ToolFactEvidence


def test_memory_grounded_claims_keep_current_user_and_successful_non_recall_tools() -> None:
    claims = (
        GroundedFinalClaimEvidence("현재 사용자 직접 근거", ("user:current",)),
        GroundedFinalClaimEvidence("웹 근거", ("tool:2:web_search",)),
        GroundedFinalClaimEvidence("과거 사용자 문맥 근거", ("user:context:4",)),
        GroundedFinalClaimEvidence("리콜 근거", ("tool:1:memory_recall",)),
        GroundedFinalClaimEvidence(
            "혼합 근거",
            ("tool:1:memory_recall", "tool:2:web_search"),
        ),
    )
    tool_evidence = (
        ToolFactEvidence("tool:2:web_search", "web_search", "fresh web result"),
    )

    filtered = _memory_grounded_final_claims(claims, tool_evidence)

    assert filtered == (
        GroundedFinalClaimEvidence("현재 사용자 직접 근거", ("user:current",)),
        GroundedFinalClaimEvidence("웹 근거", ("tool:2:web_search",)),
        GroundedFinalClaimEvidence("혼합 근거", ("tool:2:web_search",)),
    )


def test_user_approval_is_not_promoted_into_memory_without_direct_or_tool_support() -> None:
    claims = (
        GroundedFinalClaimEvidence(
            "직전 assistant가 말한 사실",
            ("user:context:7",),
        ),
    )

    assert _memory_grounded_final_claims(claims, ()) == ()
