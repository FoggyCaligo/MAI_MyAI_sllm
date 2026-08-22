from __future__ import annotations

import pytest

from mai.agent_memory import AgentGraphMemoryService
from mai.graph import GraphRepository, GraphSourceStore
from mai.model import ModelContractError


def _memory(tmp_path):
    path = tmp_path / "graph.db"
    repo = GraphRepository(path)
    sources = GraphSourceStore(path)
    memory = AgentGraphMemoryService(repo, source_store=sources)
    state = memory.begin_turn(
        user_id="owner",
        turn_id="turn-1",
        user_text="나는 Mai와 Machi를 개발하고 있다.",
    )
    return repo, sources, memory, state


def _generate(memory, state, name: str, *, kind: str = "concept", members=None):
    memory.recall(arguments={"query": name}, state=state)
    arguments = {"kind": kind, "name": name, "source_ids": [1]}
    if members is not None:
        arguments["member_node_ids"] = members
    return memory.generate_node(arguments=arguments, state=state)


def test_one_current_edge_per_ordered_pair_but_reverse_is_allowed(tmp_path) -> None:
    repo, sources, memory, state = _memory(tmp_path)
    try:
        a = _generate(memory, state, "Mai")
        b = _generate(memory, state, "Machi")

        forward = memory.generate_edge(
            arguments={
                "start_node_id": a["node_id"],
                "end_node_id": b["node_id"],
                "relation": "develops ideas used by",
                "weight": 0.8,
                "personal_relevance": "general_knowledge",
                "source_ids": [1],
            },
            state=state,
        )

        with pytest.raises(ModelContractError, match="directed memory edge already exists"):
            memory.generate_edge(
                arguments={
                    "start_node_id": a["node_id"],
                    "end_node_id": b["node_id"],
                    "relation": "another wording",
                    "weight": 0.7,
                    "personal_relevance": "general_knowledge",
                    "source_ids": [1],
                },
                state=state,
            )

        reverse = memory.generate_edge(
            arguments={
                "start_node_id": b["node_id"],
                "end_node_id": a["node_id"],
                "relation": "influences",
                "weight": 0.6,
                "personal_relevance": "general_knowledge",
                "source_ids": [1],
            },
            state=state,
        )

        assert forward["start_node_id"] == reverse["end_node_id"]
        assert forward["end_node_id"] == reverse["start_node_id"]
        assert forward["edge_id"] != reverse["edge_id"]
    finally:
        sources.close()
        repo.close()


def test_fix_edge_uses_weight_delta_and_promotes_personal_relevance(tmp_path) -> None:
    repo, sources, memory, state = _memory(tmp_path)
    try:
        a = _generate(memory, state, "사용자 프로젝트")
        b = _generate(memory, state, "Mai")
        edge = memory.generate_edge(
            arguments={
                "start_node_id": a["node_id"],
                "end_node_id": b["node_id"],
                "relation": "includes",
                "weight": 0.5,
                "personal_relevance": "general_knowledge",
                "source_ids": [1],
            },
            state=state,
        )

        fixed = memory.fix_edge(
            arguments={
                "operation": "update",
                "edge_id": edge["edge_id"],
                "relation": "is the user's active project",
                "weight_delta": 0.25,
                "personal_relevance": "user_centered",
                "source_ids": [1],
            },
            state=state,
        )

        assert fixed["weight"] == 0.75
        assert fixed["personal_relevance"] == 1.0
        assert fixed["relation"] == "is the user's active project"

        lowered = memory.fix_edge(
            arguments={
                "operation": "update",
                "edge_id": edge["edge_id"],
                "relation": "is the user's active project",
                "weight_delta": -0.1,
                "personal_relevance": "general_knowledge",
                "source_ids": [1],
            },
            state=state,
        )
        assert lowered["weight"] == pytest.approx(0.65)
        assert lowered["personal_relevance"] == 1.0
    finally:
        sources.close()
        repo.close()


def test_disconnect_keeps_edge_but_removes_it_from_active_recall(tmp_path) -> None:
    repo, sources, memory, state = _memory(tmp_path)
    try:
        a = _generate(memory, state, "A")
        b = _generate(memory, state, "B")
        edge = memory.generate_edge(
            arguments={
                "start_node_id": a["node_id"],
                "end_node_id": b["node_id"],
                "relation": "related",
                "weight": 0.9,
                "personal_relevance": "general_knowledge",
                "source_ids": [1],
            },
            state=state,
        )

        disconnected = memory.fix_edge(
            arguments={
                "operation": "disconnect",
                "edge_id": edge["edge_id"],
                "source_ids": [1],
            },
            state=state,
        )
        assert disconnected["weight"] == 0.0

        recalled = memory.recall(arguments={"node_id": a["node_id"]}, state=state)
        assert edge["edge_id"] not in {item["edge_id"] for item in recalled["edges"]}
        assert repo.get_edge(user_id="owner", edge_id=edge["edge_id"])["edge_id"] == edge["edge_id"]
    finally:
        sources.close()
        repo.close()


def test_node_and_edge_payloads_expose_source_ids(tmp_path) -> None:
    repo, sources, memory, state = _memory(tmp_path)
    try:
        a = _generate(memory, state, "A")
        b = _generate(memory, state, "B")
        edge = memory.generate_edge(
            arguments={
                "start_node_id": a["node_id"],
                "end_node_id": b["node_id"],
                "relation": "related",
                "weight": 0.8,
                "personal_relevance": "general_knowledge",
                "source_ids": [1],
            },
            state=state,
        )

        recalled = memory.recall(arguments={"node_id": a["node_id"]}, state=state)
        node = next(item for item in recalled["nodes"] if item["node_id"] == a["node_id"])
        recalled_edge = next(item for item in recalled["edges"] if item["edge_id"] == edge["edge_id"])
        assert node["source_ids"] == [1]
        assert recalled_edge["source_ids"] == [1]
    finally:
        sources.close()
        repo.close()


def test_composite_node_uses_structural_membership_and_rejects_cycle(tmp_path) -> None:
    repo, sources, memory, state = _memory(tmp_path)
    try:
        a = _generate(memory, state, "A")
        b = _generate(memory, state, "B")
        composite = _generate(
            memory,
            state,
            "A+B concept",
            kind="composite",
            members=[a["node_id"], b["node_id"]],
        )

        assert composite["kind"] == "composite"
        assert composite["member_node_ids"] == [a["node_id"], b["node_id"]]

        with pytest.raises(ModelContractError, match="cycle|itself"):
            memory.fix_node(
                arguments={
                    "operation": "set_members",
                    "node_id": composite["node_id"],
                    "member_node_ids": [a["node_id"], composite["node_id"]],
                    "source_ids": [1],
                },
                state=state,
            )
    finally:
        sources.close()
        repo.close()


def test_new_node_budget_is_ten_per_turn(tmp_path) -> None:
    repo, sources, memory, state = _memory(tmp_path)
    try:
        for index in range(10):
            _generate(memory, state, f"node-{index}")
        assert state.new_node_count == 10

        memory.recall(arguments={"query": "node-11"}, state=state)
        with pytest.raises(ModelContractError, match="new-node budget exhausted"):
            memory.generate_node(
                arguments={"kind": "concept", "name": "node-11", "source_ids": [1]},
                state=state,
            )
    finally:
        sources.close()
        repo.close()


def test_edge_mutation_budget_is_per_node_per_turn_not_permanent_degree(tmp_path) -> None:
    repo, sources, memory, state = _memory(tmp_path)
    try:
        center = _generate(memory, state, "center")
        others = [_generate(memory, state, f"other-{index}") for index in range(9)]
        # The tenth extra node would exceed the new-node budget, so use the user anchor
        # as the tenth distinct neighbor instead.
        anchor_id = min(state.available_node_ids)
        neighbors = [node["node_id"] for node in others] + [anchor_id]

        for index, neighbor in enumerate(neighbors):
            memory.generate_edge(
                arguments={
                    "start_node_id": center["node_id"],
                    "end_node_id": neighbor,
                    "relation": f"relation-{index}",
                    "weight": 0.5,
                    "personal_relevance": "general_knowledge",
                    "source_ids": [1],
                },
                state=state,
            )
        assert state.edge_mutations_by_node[center["node_id"]] == 10

        with pytest.raises(ModelContractError, match="edge mutation budget exhausted"):
            memory.fix_edge(
                arguments={
                    "operation": "update",
                    "edge_id": next(iter(state.available_edge_ids)),
                    "relation": "changed",
                    "weight_delta": 0.1,
                    "personal_relevance": "general_knowledge",
                    "source_ids": [1],
                },
                state=state,
            )
    finally:
        sources.close()
        repo.close()
