from mai.agent.failure_recovery import FAILURE_RECOVERY_SYSTEM_PROMPT
from mai.agent.tool_planner import _SYSTEM_PROMPT as TOOL_PREFLIGHT_PROMPT
from mai.app.runtime import AGENT_SYSTEM_PROMPT
from mai.memory.extraction.service import _FACT_EXTRACTION_SYSTEM


def _assert_terms(text: str, *terms: str) -> None:
    lowered = text.lower()
    for term in terms:
        assert term.lower() in lowered


def test_main_agent_prompt_is_compact_without_dropping_core_contracts() -> None:
    assert len(AGENT_SYSTEM_PROMPT) < 1800
    _assert_terms(
        AGENT_SYSTEM_PROMPT,
        "native tools",
        "do not assume access",
        "path, identifier, or target",
        "discover it",
        "tool_result_read",
        "Preserve supplied factual values unless user correct them",
        "relevant tool evidence",
        "main basis of the final answer",
        "generic advice",
        "calculator",
        "Never invent tool results",
        "report unresolved failures",
    )
    assert "Trial file_write/file_create tools are restricted" not in AGENT_SYSTEM_PROMPT
    assert "Keep source facts distinct from derived conclusions" not in AGENT_SYSTEM_PROMPT
    assert "different metrics, screens, sources, and time ranges" not in AGENT_SYSTEM_PROMPT
    assert "that specific execution failed" not in AGENT_SYSTEM_PROMPT
    assert "task is impossible" not in AGENT_SYSTEM_PROMPT


def test_preflight_prompt_is_compact_and_preserves_core_contracts() -> None:
    assert len(TOOL_PREFLIGHT_PROMPT) < 1000
    _assert_terms(
        TOOL_PREFLIGHT_PROMPT,
        "fill every boolean property",
        "latest user request",
        "recent_dialogue is only context",
        "not evidence",
        "search, inspect, verify, compare, or re-check",
        "model knowledge does not satisfy that request",
        "evidence-producing tools true",
        "path, identifier, or target",
        "both discovery and operation tools true",
        "local, memory, web, market, time, or calculation tool",
        "time-relative comparisons",
        "Do not answer the task",
        "invent tool arguments",
    )
    assert "optional-detail" not in TOOL_PREFLIGHT_PROMPT


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
