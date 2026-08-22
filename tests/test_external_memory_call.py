from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from mai.agent import AgentLifecycle
from mai.attachment_evidence import AttachmentEvidenceBuilder
from mai.final_memory import FinalMemoryExecutor
from mai.graph import GraphDiscoveryService, GraphRecallService, GraphRepository
from mai.memory_revise import ReviseMemoryTool
from mai.memory_write import WriteMemoryTool
from mai.scratchpad import ScratchpadRegistry, TurnEvidenceRegistry
from mai.working_memory_lifecycle import WorkingMemoryLifecycle


@dataclass
class FakeModel:
    actions: list[dict]
    calls: list[dict] = field(default_factory=list)

    def structured(self, *, messages: list[dict[str, str]], schema: dict) -> dict:
        self.calls.append({"messages": [dict(item) for item in messages], "schema": schema})
        if not self.actions:
            raise AssertionError("unexpected model call")
        return self.actions.pop(0)


@dataclass
class DummyAnalyzer:
    model: str = "vision"

    def analyze(self, *, path: Path, prompt: str) -> str:
        raise AssertionError("image analyzer should not run")


def test_working_lifecycle_calls_separate_memory_model_after_answer(tmp_path) -> None:
    repo = GraphRepository(tmp_path / "graph.db")
    try:
        agent_model = FakeModel(
            [{"action": "answer", "outcome": "completed", "content": "반가워, 신재용."}]
        )
        memory_model = FakeModel(
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
        evidence = TurnEvidenceRegistry()
        scratchpads = ScratchpadRegistry(evidence=evidence)
        executor = FinalMemoryExecutor(
            writer=WriteMemoryTool(repo),
            reviser=ReviseMemoryTool(repo),
            scratchpads=scratchpads,
            evidence=evidence,
        )
        discovery = GraphDiscoveryService(repo)
        agent = AgentLifecycle(
            repository=repo,
            model=agent_model,
            discovery=discovery,
            recall=GraphRecallService(repo),
            memory_executor=executor,
            work_tools=[],
        )
        lifecycle = WorkingMemoryLifecycle(
            delegate=agent,
            attachments=AttachmentEvidenceBuilder(analyzer=DummyAnalyzer()),
            evidence=evidence,
            scratchpads=scratchpads,
            memory_model=memory_model,
        )

        result = lifecycle.run(
            user_id="owner",
            user_text="나는 신재용이라고 해.",
            turn_id="turn-1",
        )

        assert result["answer"] == "반가워, 신재용."
        assert result["memory"]["mutation_count"] == 1
        assert len(agent_model.calls) == 1
        assert len(memory_model.calls) == 1
        assert agent_model is not memory_model
        assert "신재용" in memory_model.calls[0]["messages"][1]["content"]

        matches = discovery.node_lookup(user_id="owner", queries=["신재용"])["matches"]
        assert any(item["name"] == "신재용" for item in matches)
    finally:
        repo.close()
