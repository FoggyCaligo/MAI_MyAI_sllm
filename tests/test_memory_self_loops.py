from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from mai.final_memory import FinalMemoryExecutor
from mai.graph import GraphRepository
from mai.memory_completion import GraphCommitPhase
from mai.memory_revise import ReviseMemoryScope, ReviseMemoryTool
from mai.memory_write import MemorySelfLoopRejected, MemoryTurnScope, WriteMemoryTool
from mai.model import ModelContractError


def _scope(*node_ids: int) -> MemoryTurnScope:
    return MemoryTurnScope(
        user_id="owner",
        turn_id="turn-1",
        user_text="나는 투명한 플래티넘 만년필을 가지고 있어.",
        assistant_text="그 특징을 기억할게.",
        recalled_node_ids=frozenset(node_ids),
    )


def _node(repo: GraphRepository, name: str) -> dict:
    return repo.create_node(
        user_id="owner",
        name=name,
        turn_id="seed",
        source_role="user",
        source_text=name,
    )


def _executor(repo: GraphRepository) -> FinalMemoryExecutor:
    return FinalMemoryExecutor(
        writer=WriteMemoryTool(repo),
        reviser=ReviseMemoryTool(repo),
    )


def test_write_rejects_user_anchor_self_loop(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "graph.db")
    try:
        anchor = repo.ensure_user_anchor(user_id="owner", turn_id="seed", source_text="owner")
        with pytest.raises(MemorySelfLoopRejected):
            WriteMemoryTool(repo).execute(
                arguments={
                    "subject": {"kind": "user"},
                    "relation": "owns",
                    "object": {"kind": "user"},
                },
                scope=_scope(),
            )

        assert repo.one_hop_neighborhood(user_id="owner", focus_node_id=anchor["node_id"])["edges"] == []
        assert repo.provenance_for_turn(user_id="owner", turn_id="turn-1") == []
    finally:
        repo.close()


def test_write_rejects_same_resolved_new_node_and_rolls_back(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "graph.db")
    try:
        repo.ensure_user_anchor(user_id="owner", turn_id="seed", source_text="owner")
        with pytest.raises(MemorySelfLoopRejected):
            WriteMemoryTool(repo).execute(
                arguments={
                    "subject": {"new_node": {"name": "플래티넘 만년필"}},
                    "relation": "same_as",
                    "object": {"new_node": {"name": "플래티넘 만년필"}},
                },
                scope=_scope(),
            )

        assert repo.lookup_nodes(user_id="owner", queries=["플래티넘 만년필"])["matches"] == []
        assert repo.provenance_for_turn(user_id="owner", turn_id="turn-1") == []
    finally:
        repo.close()


def test_revise_rejects_self_loop_and_preserves_original_edge(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "graph.db")
    try:
        repo.ensure_user_anchor(user_id="owner", turn_id="seed", source_text="owner")
        subject = _node(repo, "플래티넘 만년필")
        obj = _node(repo, "Slip & Seal")
        edge = repo.create_or_reinforce_edge(
            user_id="owner",
            subject_node_id=subject["node_id"],
            relation="has_feature",
            object_node_id=obj["node_id"],
            turn_id="seed-edge",
            source_role="user",
            source_text="seed",
        )
        turn = _scope(subject["node_id"], obj["node_id"])
        scope = ReviseMemoryScope(
            turn=turn,
            eligible_node_ids=frozenset({subject["node_id"], obj["node_id"]}),
            eligible_edge_ids=frozenset({edge["edge_id"]}),
        )

        with pytest.raises(MemorySelfLoopRejected):
            ReviseMemoryTool(repo).execute(
                arguments={
                    "edge_id": edge["edge_id"],
                    "subject": {"existing_node_id": subject["node_id"]},
                    "relation": "has_feature",
                    "object": {"existing_node_id": subject["node_id"]},
                },
                scope=scope,
            )

        unchanged = repo.get_edge(user_id="owner", edge_id=edge["edge_id"])
        assert unchanged["subject_node_id"] == subject["node_id"]
        assert unchanged["object_node_id"] == obj["node_id"]
    finally:
        repo.close()


@dataclass
class RetryModel:
    actions: list[dict]
    messages_seen: list[list[dict[str, str]]] = field(default_factory=list)

    def structured(self, *, messages: list[dict[str, str]], schema: dict) -> dict:
        self.messages_seen.append([dict(item) for item in messages])
        if not self.actions:
            raise AssertionError("unexpected model round")
        return self.actions.pop(0)


def test_graph_commit_retries_rejected_self_loop_then_writes_distinct_node(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "graph.db")
    try:
        anchor = repo.ensure_user_anchor(user_id="owner", turn_id="seed", source_text="owner")
        model = RetryModel(
            actions=[
                {
                    "action": "tool",
                    "tool": "write_memory",
                    "arguments": {
                        "subject": {"kind": "user"},
                        "relation": "owns",
                        "object": {"kind": "user"},
                    },
                    "continue_memory": False,
                },
                {
                    "action": "tool",
                    "tool": "write_memory",
                    "arguments": {
                        "subject": {"kind": "user"},
                        "relation": "owns",
                        "object": {"new_node": {"name": "플래티넘 만년필"}},
                    },
                    "continue_memory": False,
                },
            ]
        )

        result = GraphCommitPhase(model=model, executor=_executor(repo)).run(
            user_id="owner",
            turn_id="turn-1",
            user_text="나는 투명한 플래티넘 만년필을 가지고 있어.",
            fixed_answer="그 만년필을 기억할게.",
            recall_result=None,
        )

        assert result["mutation_count"] == 1
        assert len(model.messages_seen) == 2
        assert "self_loop" in model.messages_seen[1][-1]["content"]
        edge = result["mutations"][0]["edge"]
        assert edge["subject_node_id"] == anchor["node_id"]
        assert edge["subject_node_id"] != edge["object_node_id"]
        assert result["mutations"][0]["created_nodes"][0]["name"] == "플래티넘 만년필"
    finally:
        repo.close()


def test_graph_commit_fails_on_identical_repeated_rejected_self_loop(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "graph.db")
    try:
        repo.ensure_user_anchor(user_id="owner", turn_id="seed", source_text="owner")
        rejected = {
            "action": "tool",
            "tool": "write_memory",
            "arguments": {
                "subject": {"kind": "user"},
                "relation": "owns",
                "object": {"kind": "user"},
            },
            "continue_memory": False,
        }
        model = RetryModel(actions=[rejected, dict(rejected)])

        with pytest.raises(ModelContractError, match="already rejected self-loop"):
            GraphCommitPhase(model=model, executor=_executor(repo)).run(
                user_id="owner",
                turn_id="turn-1",
                user_text="나는 만년필을 가지고 있어.",
                fixed_answer="기억할게.",
                recall_result=None,
            )
    finally:
        repo.close()
