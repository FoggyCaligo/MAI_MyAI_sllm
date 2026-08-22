from dataclasses import dataclass
from typing import Any

from mai.agent import AgentLifecycle, WorkContext
from mai.graph import GraphDiscoveryService, GraphRecallService, GraphRepository, GraphSourceStore, SourceRecord


@dataclass
class ScriptedModel:
    actions: list[dict[str, Any]]

    def structured(self, *, messages, schema):
        return self.actions.pop(0)


class NoScratchpadMemoryExecutor:
    def available_scratchpad_ids(self, *, turn_id: str) -> frozenset[str]:
        return frozenset()


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


def test_agent_opens_graph_sources_only_after_summary(tmp_path) -> None:
    db = tmp_path / "graph.sqlite3"
    repository = GraphRepository(db)
    sources = GraphSourceStore(db)
    try:
        anchor = repository.ensure_user_anchor(user_id="u", turn_id="seed", source_text="seed")
        memory = repository.create_node(
            user_id="u",
            name="project",
            turn_id="seed",
            source_role="user",
            source_text="project",
        )
        edge = repository.create_or_reinforce_edge(
            user_id="u",
            subject_node_id=int(anchor["node_id"]),
            relation="works_on",
            object_node_id=int(memory["node_id"]),
            turn_id="seed",
            source_role="turn",
            source_text="seed",
        )
        with repository.transaction() as conn:
            source_ids = sources.ensure_sources_in_connection(
                conn,
                user_id="u",
                turn_id="seed-source",
                records=[
                    SourceRecord(
                        source_kind="user_message",
                        source_key="user",
                        content="I am working on project",
                        metadata={},
                    )
                ],
            )
            sources.link_sources_in_connection(
                conn,
                user_id="u",
                turn_id="seed-source",
                source_ids=source_ids,
                edge_id=int(edge["edge_id"]),
            )

        model = ScriptedModel(
            [
                {"action": "tool", "tool": "node_lookup", "arguments": {"queries": ["project"]}},
                {
                    "action": "tool",
                    "tool": "recall_memory",
                    "arguments": {"focus_node_id": int(memory["node_id"])},
                },
                {
                    "action": "tool",
                    "tool": "memory_source_summary",
                    "arguments": {"edge_id": int(edge["edge_id"])},
                },
                {
                    "action": "tool",
                    "tool": "memory_source_read",
                    "arguments": {"source_id": source_ids[0], "limit": 100},
                },
                _answer(),
            ]
        )
        lifecycle = AgentLifecycle(
            repository=repository,
            model=model,
            discovery=GraphDiscoveryService(repository),
            recall=GraphRecallService(repository, source_store=sources),
            memory_executor=NoScratchpadMemoryExecutor(),  # type: ignore[arg-type]
            work_tools=[],
            source_store=sources,
        )

        answer, _, events = lifecycle._run_agent_phase(
            context=WorkContext(user_id="u", turn_id="turn", user_text="project?"),
            candidate_ids=set(),
            recall_results=[],
        )

        assert answer == "done"
        assert [event["tool"] for event in events] == [
            "node_lookup",
            "recall_memory",
            "memory_source_summary",
            "memory_source_read",
        ]
        recall_result = events[1]["result"]
        recalled_edge = next(item for item in recall_result["edges"] if item["edge_id"] == edge["edge_id"])
        assert recalled_edge["source_kind"] == "user_message"
        assert "I am working on project" not in str(recall_result)
        assert "I am working on project" not in str(events[2]["result"])
        assert events[3]["result"]["content"] == "I am working on project"
    finally:
        sources.close()
        repository.close()
