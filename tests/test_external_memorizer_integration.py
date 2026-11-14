"""The MAI memory engine and fact extraction are native again."""
from mai.memory.graph.repository import MemoryGraphRepository
from mai.memory.runtime import MemoryRuntime
from mai.memory.segmenter import SentenceBreakerSegmenter
from mai.memory.extraction.service import _FACT_EXTRACTION_SYSTEM


def test_native_memory_extraction_contract():
    assert "durable, user-grounded facts" in _FACT_EXTRACTION_SYSTEM
    assert "assistant_final_answer" in _FACT_EXTRACTION_SYSTEM
    assert "facts" in _FACT_EXTRACTION_SYSTEM


def test_application_uses_native_memory_classes():
    from mai.app import runtime
    assert runtime.MemoryGraphRepository is MemoryGraphRepository
    assert runtime.MemoryRuntime is MemoryRuntime
    assert runtime.SentenceBreakerSegmenter is SentenceBreakerSegmenter
