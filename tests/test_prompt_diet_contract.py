from mai.agent.failure_recovery import FAILURE_RECOVERY_SYSTEM_PROMPT
from mai.agent.tool_planner import _SYSTEM_PROMPT as TOOL_PREFLIGHT_PROMPT
from mai.app.runtime import AGENT_SYSTEM_PROMPT
from mai.memory.extraction.service import _FACT_EXTRACTION_SYSTEM


def _assert_terms(text: str, *terms: str) -> None:
    lowered = text.lower()
    for term in terms:
        assert term.lower() in lowered


def test_main_agent_prompt_is_compact_without_dropping_core_contracts() -> None:
    assert len(AGENT_SYSTEM_PROMPT) < 2200
    _assert_terms(
        AGENT_SYSTEM_PROMPT,
        "native tools",
        "do not assume access",
        "path, identifier, or target",
        "discover it",
        "tool_result_read",
        "Preserve supplied factual values",
        "different metrics",
        "calculator",
        "that specific execution failed",
        "task is impossible",
        "report unresolved failures",
    )


def test_preflight_prompt_is_compact_and_keeps_prerequisite_discovery() -> None:
    assert len(TOOL_PREFLIGHT_PROMPT) < 1500
    _assert_terms(
        TOOL_PREFLIGHT_PROMPT,
        "exact available tool names",
        "must produce an execution result",
        "input identifier, path, or target that is not yet established",
        "require both the discovery tool and the operation tool",
        "local inspection/action",
        "memory",
        "web/market/time/calculation",
        "current-time tool",
        "optional detail",
        "Do not call tools",
    )


def test_preflight_requires_evidence_tools_when_research_is_the_requested_method() -> None:
    _assert_terms(
        TOOL_PREFLIGHT_PROMPT,
        "requested method or deliverable",
        "discover, search, inspect, verify, compare, or re-check",
        "model-training knowledge is not the requested evidence",
        "require tools that produce that evidence",
        "even for stable facts",
    )


def test_fact_extraction_prompt_is_compact_and_preserves_evidence_contract() -> None:
    assert len(_FACT_EXTRACTION_SYSTEM) < 1200
    _assert_terms(
        _FACT_EXTRACTION_SYSTEM,
        "latest user message",
        "primary evidence",
        "assistant answer is context",
        "Persistent-memory recall is absent",
        "Pure recall questions normally produce no facts",
        "mixed messages",
        "Do not invent",
        "Deduplicate",
    )


def test_failure_recovery_prompt_is_compact_and_truthful() -> None:
    assert len(FAILURE_RECOVERY_SYSTEM_PROMPT) < 800
    _assert_terms(
        FAILURE_RECOVERY_SYSTEM_PROMPT,
        "real MAI agent failure",
        "Do not hide the failure",
        "tool evidence",
        "partial",
        "unknown/incomplete",
        "No tools are available",
    )
