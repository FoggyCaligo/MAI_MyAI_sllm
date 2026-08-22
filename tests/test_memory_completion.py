from __future__ import annotations

from dataclasses import dataclass, field

from mai.agent import AgentLifecycle
from mai.final_memory import FinalMemoryExecutor
from mai.graph import GraphDiscoveryService, GraphRecallService, GraphRepository
from mai.memory_completion import GraphCommitPhase
from mai.memory_revise import ReviseMemoryTool
from mai.memory_write import WriteMemoryTool


@dataclass
class FakeModel:
    actions: list[dict]
    schemas: list[dict] = field(default_factory=list)
    messages_seen: list[list[dict[str, str]]] = field(default_factory=list)

    def structured(self, *, messages: list[dict[str, str]], schema: dict) -> dict:
        self.schemas.append(schema)
        self.messages_seen.append([dict(item) for item in messages])
        if not self.actions:
            raise AssertionError("unexpected model round")
        return self.actions.pop(0)


def _variants(schema: dict) -> list[dict]:
    return schema.get("oneOf", [schema])


def _tool_names(schema: dict) -> set[str]:
    names: set[str] = set()
    for variant in _variants(schema):
        tool = variant.get("properties", {}).get("tool", {}).get("const")
        if tool:
            names.add(str(tool))
    return names


def _executor(repo: GraphRepository) -> FinalMemoryExecutor:
    return FinalMemoryExecutor(
        writer=WriteMemoryTool(repo),
        reviser=ReviseMemoryTool(repo),
    )


def test_one_memory_mutation_finishes_without_a_done_round(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "graph.db")
    try:
        repo.ensure_user_anchor(user_id="owner", turn_id="seed", source_text="owner")
        model = FakeModel(
            [
                {
                    "action": "tool",
                    "tool": "write_memory",
                    "arguments": {
                        "subject": {"kind": "user"},
                        "relation": "name",
                        "object": {"new_node": {"name": "신재용"}},
                    },
                    "continue_memory": False,
                }
            ]
        )
        result = GraphCommitPhase(model=model, executor=_executor(repo)).run(
            user_id="owner",
            turn_id="turn-1",
            user_text="내 이름은 신재용이야.",
            fixed_answer="반가워, 신재용.",
            recall_result=None,
        )

        assert result["status"] == "done"
        assert result["mutation_count"] == 1
        assert len(model.schemas) == 1
        assert model.schemas[0]["properties"]["continue_memory"] == {"type": "boolean"}
        assert model.schemas[0]["properties"]["tool"] == {"const": "write_memory"}
    finally:
        repo.close()


def test_current_turn_written_edge_is_immediately_revisable(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "graph.db")
    try:
        repo.ensure_user_anchor(user_id="owner", turn_id="seed", source_text="owner")

        class WriteThenReviseModel(FakeModel):
            def structured(self, *, messages: list[dict[str, str]], schema: dict) -> dict:
                self.schemas.append(schema)
                self.messages_seen.append([dict(item) for item in messages])
                if len(self.schemas) == 1:
                    return {
                        "action": "tool",
                        "tool": "write_memory",
                        "arguments": {
                            "subject": {"kind": "user"},
                            "relation": "introduced_self",
                            "object": {"new_node": {"name": "신재용"}},
                        },
                        "continue_memory": True,
                    }

                revise_variant = next(
                    item
                    for item in _variants(schema)
                    if item.get("properties", {}).get("tool") == {"const": "revise_memory"}
                )
                revise_props = revise_variant["properties"]["arguments"]["properties"]
                edge_id = revise_props["edge_id"]["enum"][0]
                object_options = revise_props["object"]["oneOf"]
                existing = next(
                    item for item in object_options if "existing_node_id" in item.get("properties", {})
                )
                node_id = existing["properties"]["existing_node_id"]["enum"][0]
                return {
                    "action": "tool",
                    "tool": "revise_memory",
                    "arguments": {
                        "edge_id": edge_id,
                        "subject": {"kind": "user"},
                        "relation": "name",
                        "object": {"existing_node_id": node_id},
                    },
                    "continue_memory": False,
                }

        model = WriteThenReviseModel([])
        result = GraphCommitPhase(model=model, executor=_executor(repo)).run(
            user_id="owner",
            turn_id="turn-1",
            user_text="내 이름은 신재용이야.",
            fixed_answer="반가워, 신재용.",
            recall_result=None,
        )

        assert result["mutation_count"] == 2
        assert "revise_memory" not in _tool_names(model.schemas[0])
        assert "revise_memory" in _tool_names(model.schemas[1])
        assert result["mutations"][1]["status"] == "revised"
        assert result["mutations"][1]["edge"]["relation"] == "name"
    finally:
        repo.close()


def test_agent_lifecycle_adds_one_narrow_memory_call_after_answer(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "graph.db")
    try:
        model = FakeModel(
            [
                {"action": "answer", "outcome": "completed", "content": "반가워, 신재용."},
                {
                    "action": "tool",
                    "tool": "write_memory",
                    "arguments": {
                        "subject": {"kind": "user"},
                        "relation": "name",
                        "object": {"new_node": {"name": "신재용"}},
                    },
                    "continue_memory": False,
                },
            ]
        )
        lifecycle = AgentLifecycle(
            repository=repo,
            model=model,
            discovery=GraphDiscoveryService(repo),
            recall=GraphRecallService(repo),
            memory_executor=_executor(repo),
            work_tools=[],
        )

        result = lifecycle.run(
            user_id="owner",
            user_text="내 이름은 신재용이야.",
            turn_id="turn-1",
        )

        assert result["answer"] == "반가워, 신재용."
        assert result["memory"]["mutation_count"] == 1
        assert len(model.schemas) == 2

        answer_variant = next(
            item
            for item in _variants(model.schemas[0])
            if item.get("properties", {}).get("action") == {"const": "answer"}
        )
        assert "memory_mutations" not in answer_variant["properties"]
        assert "memory_mutations" not in answer_variant["required"]
        assert model.schemas[1]["properties"]["continue_memory"] == {"type": "boolean"}

        memory_messages = model.messages_seen[1]
        assert len(memory_messages) == 2
        assert memory_messages[0]["role"] == "system"
        assert memory_messages[1]["role"] == "user"
        assert "반가워, 신재용." in memory_messages[1]["content"]
        assert "내 이름은 신재용이야." in memory_messages[1]["content"]
    finally:
        repo.close()
