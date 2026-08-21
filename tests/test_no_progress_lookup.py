from dataclasses import dataclass, field
from typing import Any

from mai.agent import AgentLifecycle, WorkContext
from mai.memory_discovery import MandatoryMemoryDiscovery


@dataclass
class ScriptedModel:
    actions: list[dict[str, Any]]
    schemas: list[dict[str, Any]] = field(default_factory=list)

    def structured(self, *, messages, schema):
        self.schemas.append(schema)
        return self.actions.pop(0)


@dataclass
class ScriptedDiscovery:
    results: list[dict[str, Any]]

    def node_lookup(self, *, user_id, queries):
        return self.results.pop(0)


@dataclass
class FakeRecall:
    def recall_one_depth(self, *, user_id, focus_node_id):
        return {"nodes": [], "edges": [], "origin_path": {"nodes": [], "edges": []}}


def _answer() -> dict[str, Any]:
    return {
        "action": "answer",
        "content": "done",
        "memory_mutations": [
            {
                "kind": "write_memory",
                "arguments": {
                    "subject": {"kind": "user"},
                    "relation": "turn_memory",
                    "object": {"new_node": {"name": "done"}},
                },
            }
        ],
    }


def _has_node_lookup(schema: dict[str, Any]) -> bool:
    return '"node_lookup"' in __import__("json").dumps(schema)


def test_work_disables_lookup_after_no_candidate_progress() -> None:
    model = ScriptedModel([
        {"action": "tool", "tool": "node_lookup", "arguments": {"queries": ["x"]}},
        _answer(),
    ])
    lifecycle = AgentLifecycle(
        repository=None,
        model=model,
        discovery=ScriptedDiscovery([{"matches": [{"node_id": 1}]}]),
        recall=FakeRecall(),
        memory_executor=None,
        work_tools=[],
    )

    answer, _, events = lifecycle._run_work_phase(
        context=WorkContext(user_id="u", turn_id="t", user_text="hello"),
        candidate_ids={1},
        recall_results=[],
    )

    assert answer == "done"
    assert len(events) == 1
    assert _has_node_lookup(model.schemas[0])
    assert not _has_node_lookup(model.schemas[1])


def test_work_keeps_lookup_when_candidate_set_expands() -> None:
    model = ScriptedModel([
        {"action": "tool", "tool": "node_lookup", "arguments": {"queries": ["x"]}},
        _answer(),
    ])
    lifecycle = AgentLifecycle(
        repository=None,
        model=model,
        discovery=ScriptedDiscovery([{"matches": [{"node_id": 1}, {"node_id": 2}]}]),
        recall=FakeRecall(),
        memory_executor=None,
        work_tools=[],
    )

    lifecycle._run_work_phase(
        context=WorkContext(user_id="u", turn_id="t", user_text="hello"),
        candidate_ids={1},
        recall_results=[],
    )

    assert _has_node_lookup(model.schemas[0])
    assert _has_node_lookup(model.schemas[1])


def test_discovery_disables_additional_lookup_after_no_progress() -> None:
    model = ScriptedModel([
        {"action": "tool", "tool": "node_lookup", "arguments": {"queries": ["first"]}},
        {"action": "tool", "tool": "node_lookup", "arguments": {"queries": ["again"]}},
        {"action": "tool", "tool": "recall_memory", "arguments": {"focus_node_id": 1}},
    ])
    discovery = MandatoryMemoryDiscovery(
        model=model,
        discovery=ScriptedDiscovery([
            {"matches": [{"node_id": 1}]},
            {"matches": [{"node_id": 1}]},
        ]),
        recall=FakeRecall(),
    )

    result = discovery.run(user_id="u", user_text="hello")

    assert result["status"] == "recalled"
    assert _has_node_lookup(model.schemas[0])
    assert _has_node_lookup(model.schemas[1])
    assert not _has_node_lookup(model.schemas[2])
