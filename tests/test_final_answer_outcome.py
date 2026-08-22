from __future__ import annotations

from mai.agent import _answer_schema


def test_final_answer_schema_requires_structured_outcome_without_memory_plan() -> None:
    schema = _answer_schema()
    assert "outcome" in schema["required"]
    assert schema["properties"]["outcome"]["enum"] == ["completed", "blocked"]
    assert "memory_mutations" not in schema["properties"]
    assert "memory_mutations" not in schema["required"]
