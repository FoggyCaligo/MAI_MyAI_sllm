from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from mai.agent import AgentLifecycle, FunctionWorkTool
from mai.graph import GraphDiscoveryService, GraphRecallService, GraphRepository
from mai.model import ModelContractError


@dataclass
class FakeModel:
    actions: list[dict]
    schemas: list[dict] = field(default_factory=list)

    def structured(self, *, messages: list[dict[str, str]], schema: dict) -> dict:
        self.schemas.append(schema)
        if not self.actions:
            raise AssertionError("unexpected model round")
        return self.actions.pop(0)


@dataclass
class FakeDiscoveryPhase:
    result: dict
    calls: list[tuple[str, str]] = field(default_factory=list)

    def run(self, *, user_id: str, user_text: str) -> dict:
        self.calls.append((user_id, user_text))
        return self.result


@dataclass
class FakeMemoryCompletion:
    result: dict
    calls: list[tuple[object, object]] = field(default_factory=list)
    error: Exception | None = None

    def run(self, *, turn, recall_result):
        self.calls.append((turn, recall_result))
        if self.error:
            raise self.error
        return self.result


def _node(repo: GraphRepository, name: str) -> dict:
    return repo.create_node(
        user_id="owner",
        name=name,
        turn_id="seed",
        source_role="user",
        source_text=name,
    )


def _edge(repo: GraphRepository, a: int, relation: str, b: int) -> dict:
    return repo.create_or_reinforce_edge(
        user_id="owner",
        subject_node_id=a,
        relation=relation,
        object_node_id=b,
        turn_id="seed",
        source_role="turn",
        source_text=relation,
    )


def _lifecycle(repo, model, discovery_phase, completion, tools=None):
    return AgentLifecycle(
        repository=repo,
        model=model,
        discovery_phase=discovery_phase,
        discovery=GraphDiscoveryService(repo),
        recall=GraphRecallService(repo),
        memory_completion=completion,
        work_tools=tools or [],
    )


def test_discovery_precedes_work_and_answer_releases_after_memory_done(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    try:
        discovery = FakeDiscoveryPhase({"status": "no_match", "lookup": {"matches": []}, "recall": None})
        completion = FakeMemoryCompletion({"status": "done", "mutation_count": 1, "mutations": []})
        model = FakeModel([{"action": "answer", "content": "fixed"}])

        result = _lifecycle(repo, model, discovery, completion).run(
            user_id="owner", user_text="hello", turn_id="t1"
        )

        assert discovery.calls == [("owner", "hello")]
        assert len(completion.calls) == 1
        turn, _ = completion.calls[0]
        assert turn.assistant_text == "fixed"
        assert result["answer"] == "fixed"
        assert result["status"] == "completed"
    finally:
        repo.close()


def test_work_tool_is_selected_by_structured_action_and_result_returns_to_model(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    try:
        calls = []

        def handler(arguments, context):
            calls.append((arguments, context.turn_id))
            return {"value": arguments["value"] * 2}

        tool = FunctionWorkTool(
            name="double",
            description="Double a number",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["value"],
                "properties": {"value": {"type": "integer"}},
            },
            handler=handler,
        )
        model = FakeModel([
            {"action": "tool", "tool": "double", "arguments": {"value": 4}},
            {"action": "answer", "content": "8"},
        ])
        discovery = FakeDiscoveryPhase({"status": "no_match", "lookup": {"matches": []}, "recall": None})
        completion = FakeMemoryCompletion({"status": "done", "mutation_count": 1, "mutations": []})

        result = _lifecycle(repo, model, discovery, completion, [tool]).run(
            user_id="owner", user_text="double", turn_id="t1"
        )

        assert calls == [({"value": 4}, "t1")]
        assert result["work_events"][0]["result"] == {"value": 8}
        first_schema = model.schemas[0]
        variants = first_schema["oneOf"]
        assert any(v.get("properties", {}).get("tool") == {"const": "double"} for v in variants)
    finally:
        repo.close()


def test_additional_lookup_and_recall_are_included_in_memory_scope(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    try:
        anchor = repo.ensure_user_anchor(user_id="owner", turn_id="seed", source_text="owner")
        a = _node(repo, "MAI")
        edge = _edge(repo, anchor["node_id"], "has", a["node_id"])
        discovery = FakeDiscoveryPhase({"status": "no_match", "lookup": {"matches": []}, "recall": None})
        completion = FakeMemoryCompletion({"status": "done", "mutation_count": 1, "mutations": []})
        model = FakeModel([
            {"action": "tool", "tool": "node_lookup", "arguments": {"queries": ["MAI"]}},
            {"action": "tool", "tool": "recall_memory", "arguments": {"focus_node_id": a["node_id"]}},
            {"action": "answer", "content": "remembered"},
        ])

        _lifecycle(repo, model, discovery, completion).run(
            user_id="owner", user_text="MAI", turn_id="t1"
        )

        turn, aggregate = completion.calls[0]
        assert a["node_id"] in turn.recalled_node_ids
        assert anchor["node_id"] in turn.recalled_node_ids
        assert edge["edge_id"] in {item["edge_id"] for item in aggregate["edges"]}
    finally:
        repo.close()


def test_recall_cannot_use_unlooked_up_id(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    try:
        a = _node(repo, "secret")
        discovery = FakeDiscoveryPhase({"status": "no_match", "lookup": {"matches": []}, "recall": None})
        completion = FakeMemoryCompletion({"status": "done", "mutation_count": 1, "mutations": []})
        model = FakeModel([
            {"action": "tool", "tool": "recall_memory", "arguments": {"focus_node_id": a["node_id"]}},
        ])

        with pytest.raises(ModelContractError):
            _lifecycle(repo, model, discovery, completion).run(
                user_id="owner", user_text="x", turn_id="t1"
            )
    finally:
        repo.close()


def test_fixed_answer_is_not_returned_when_memory_completion_fails(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    try:
        discovery = FakeDiscoveryPhase({"status": "no_match", "lookup": {"matches": []}, "recall": None})
        completion = FakeMemoryCompletion(
            {"status": "done"}, error=RuntimeError("memory failure")
        )
        model = FakeModel([{"action": "answer", "content": "must not release"}])

        with pytest.raises(RuntimeError, match="memory failure"):
            _lifecycle(repo, model, discovery, completion).run(
                user_id="owner", user_text="hello", turn_id="t1"
            )
    finally:
        repo.close()


def test_work_loop_has_no_arbitrary_round_cap(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "g.db")
    try:
        count = 25
        calls = []

        def handler(arguments, context):
            calls.append(arguments["n"])
            return arguments["n"]

        tool = FunctionWorkTool(
            name="echo_number",
            description="Echo a number",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["n"],
                "properties": {"n": {"type": "integer"}},
            },
            handler=handler,
        )
        actions = [
            {"action": "tool", "tool": "echo_number", "arguments": {"n": n}}
            for n in range(count)
        ] + [{"action": "answer", "content": "done"}]
        model = FakeModel(actions)
        discovery = FakeDiscoveryPhase({"status": "no_match", "lookup": {"matches": []}, "recall": None})
        completion = FakeMemoryCompletion({"status": "done", "mutation_count": 1, "mutations": []})

        result = _lifecycle(repo, model, discovery, completion, [tool]).run(
            user_id="owner", user_text="loop", turn_id="t1"
        )

        assert calls == list(range(count))
        assert result["answer"] == "done"
    finally:
        repo.close()
