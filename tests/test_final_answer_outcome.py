from __future__ import annotations

from mai.final_memory import answer_with_memory_schema


def test_final_answer_schema_requires_structured_outcome() -> None:
    schema = answer_with_memory_schema(None)
    assert "outcome" in schema["required"]
    assert schema["properties"]["outcome"]["enum"] == ["completed", "blocked"]
