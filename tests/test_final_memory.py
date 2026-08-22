from __future__ import annotations

import pytest

from mai.final_memory import FinalMemoryExecutor, answer_with_memory_schema
from mai.graph import GraphRepository
from mai.memory_revise import ReviseMemoryTool
from mai.memory_write import WriteMemoryTool
from mai.model import ModelContractError


def test_answer_schema_requires_at_least_one_memory_mutation() -> None:
    schema = answer_with_memory_schema(None)
    assert schema["required"] == ["action", "content", "memory_mutations"]
    assert schema["properties"]["memory_mutations"]["minItems"] == 1


def test_answer_schema_exposes_revise_only_for_recalled_edges() -> None:
    without_recall = answer_with_memory_schema(None)
    item_without = without_recall["properties"]["memory_mutations"]["items"]
    assert item_without["properties"]["kind"] == {"const": "write_memory"}

    with_recall = answer_with_memory_schema(
        {
            "nodes": [{"node_id": 3}],
            "edges": [{"edge_id": 7}],
            "origin_path": {"nodes": [], "edges": []},
        }
    )
    item_with = with_recall["properties"]["memory_mutations"]["items"]
    kinds = {
        variant["properties"]["kind"]["const"]
        for variant in item_with["oneOf"]
    }
    assert kinds == {"write_memory", "revise_memory"}


def test_executor_commits_multiple_planned_mutations_without_model_rounds(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    try:
        repo.ensure_user_anchor(user_id="owner", turn_id="seed", source_text="owner")
        executor = FinalMemoryExecutor(WriteMemoryTool(repo), ReviseMemoryTool(repo))
        result = executor.execute(
            user_id="owner",
            turn_id="t1",
            user_text="hello",
            fixed_answer="hi",
            recall_result=None,
            mutations=[
                {
                    "kind": "write_memory",
                    "arguments": {
                        "subject": {"kind": "user"},
                        "relation": "said",
                        "object": {"new_node": {"name": "hello"}},
                    },
                },
                {
                    "kind": "write_memory",
                    "arguments": {
                        "subject": {"kind": "user"},
                        "relation": "received",
                        "object": {"new_node": {"name": "hi"}},
                    },
                },
            ],
        )
        assert result["status"] == "done"
        assert result["mutation_count"] == 2
        assert len(result["mutations"]) == 2
    finally:
        repo.close()


def test_executor_rejects_empty_plan() -> None:
    executor = FinalMemoryExecutor(None, None)  # type: ignore[arg-type]
    with pytest.raises(ModelContractError, match="at least one memory mutation"):
        executor.execute(
            user_id="owner",
            turn_id="t1",
            user_text="hello",
            fixed_answer="hi",
            recall_result=None,
            mutations=[],
        )
