from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from mai.final_memory import FinalMemoryExecutor, answer_with_memory_schema
from mai.graph import GraphRepository
from mai.memory_revise import ReviseMemoryTool
from mai.memory_write import WriteMemoryTool
from mai.model import ModelContractError


def _variants(schema: dict[str, Any]) -> list[dict[str, Any]]:
    return schema.get("oneOf", [schema])


def _tool_names(schema: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for variant in _variants(schema):
        kind = (variant.get("properties") or {}).get("kind") or {}
        if "const" in kind:
            names.add(str(kind["const"]))
    return names


def test_answer_schema_requires_at_least_one_memory_mutation() -> None:
    schema = answer_with_memory_schema(None)
    mutations = schema["properties"]["memory_mutations"]
    assert mutations["minItems"] == 1
    assert _tool_names(mutations["items"]) == {"write_memory"}


def test_answer_schema_exposes_revise_only_for_recalled_edge() -> None:
    recall = {
        "nodes": [{"node_id": 1}],
        "edges": [{"edge_id": 9}],
        "origin_path": {"nodes": [], "edges": []},
    }
    schema = answer_with_memory_schema(recall)
    assert _tool_names(schema["properties"]["memory_mutations"]["items"]) == {
        "write_memory",
        "revise_memory",
    }


def test_final_executor_commits_planned_mutations_without_model_call(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    try:
        executor = FinalMemoryExecutor(
            writer=WriteMemoryTool(repo),
            reviser=ReviseMemoryTool(repo),
        )
        result = executor.execute(
            user_id="owner",
            turn_id="turn-1",
            user_text="hello",
            fixed_answer="fixed",
            recall_result=None,
            mutations=[
                {
                    "kind": "write_memory",
                    "arguments": {
                        "subject": {"kind": "user"},
                        "relation": "remembered",
                        "object": {"new_node": {"name": "fixed"}},
                    },
                }
            ],
        )
        assert result["status"] == "done"
        assert result["mutation_count"] == 1
        assert result["mutations"][0]["edge"]["relation"] == "remembered"
    finally:
        repo.close()


def test_final_executor_rejects_empty_plan() -> None:
    @dataclass
    class Unused:
        calls: list = field(default_factory=list)

        def execute(self, *, arguments, scope):
            self.calls.append((arguments, scope))
            return {}

    unused = Unused()
    executor = FinalMemoryExecutor(writer=unused, reviser=unused)  # type: ignore[arg-type]
    with pytest.raises(ModelContractError, match="at least one memory mutation"):
        executor.execute(
            user_id="owner",
            turn_id="turn",
            user_text="u",
            fixed_answer="a",
            recall_result=None,
            mutations=[],
        )
    assert unused.calls == []
