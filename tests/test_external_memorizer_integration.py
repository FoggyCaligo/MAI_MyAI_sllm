"""Smoke contracts for the external MAI-derived memory engine."""
from mcp_memorizer.memory.graph.repository import MemoryGraphRepository as ExternalGraph
from mcp_memorizer.memory.runtime import MemoryRuntime as ExternalRuntime
from mcp_memorizer.memory.segmenter import SentenceBreakerSegmenter as ExternalSegmenter
from mcp_memorizer.extraction.prompt import _FACT_EXTRACTION_SYSTEM as EXTERNAL_PROMPT

def test_external_memory_contract_preserves_extraction_prompt():
    assert "latest_user_message" in EXTERNAL_PROMPT
    assert "assistant_final_answer" in EXTERNAL_PROMPT
    assert "facts" in EXTERNAL_PROMPT

def test_runtime_uses_external_memory_classes():
    from mai.app import runtime as module
    assert module.MemoryGraphRepository is ExternalGraph
    assert module.MemoryRuntime is ExternalRuntime
    assert module.SentenceBreakerSegmenter is ExternalSegmenter
