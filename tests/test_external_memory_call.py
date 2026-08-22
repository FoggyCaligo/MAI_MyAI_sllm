from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from mai.agent import AgentLifecycle
from mai.attachment_evidence import AttachmentEvidenceBuilder
from mai.final_memory import FinalMemoryExecutor
from mai.graph import GraphDiscoveryService, GraphRecallService, GraphRepository
from mai.memory_revise import ReviseMemoryTool
from mai.memory_write import WriteMemoryTool
from mai.model import OllamaModel
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


def _memory_parts(repo: GraphRepository):
    evidence = TurnEvidenceRegistry()
    scratchpads = ScratchpadRegistry(evidence=evidence)
    executor = FinalMemoryExecutor(
        writer=WriteMemoryTool(repo),
        reviser=ReviseMemoryTool(repo),
        scratchpads=scratchpads,
        evidence=evidence,
    )
    return evidence, scratchpads, executor


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
        evidence, scratchpads, executor = _memory_parts(repo)
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


def test_ollama_runtime_defaults_to_dedicated_qwen_memory_model(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("MAI_OLLAMA_MEMORY_MODEL", raising=False)
    repo = GraphRepository(tmp_path / "graph.db")
    try:
        evidence, scratchpads, executor = _memory_parts(repo)
        agent_model = OllamaModel(
            model="gemma4:e4b",
            base_url="http://127.0.0.1:11434",
            timeout_seconds=123.0,
        )
        agent = AgentLifecycle(
            repository=repo,
            model=agent_model,
            discovery=GraphDiscoveryService(repo),
            recall=GraphRecallService(repo),
            memory_executor=executor,
            work_tools=[],
        )
        lifecycle = WorkingMemoryLifecycle(
            delegate=agent,
            attachments=AttachmentEvidenceBuilder(analyzer=DummyAnalyzer()),
            evidence=evidence,
            scratchpads=scratchpads,
        )

        assert isinstance(lifecycle.memory_model, OllamaModel)
        assert lifecycle.memory_model is not agent_model
        assert lifecycle.memory_model.model == "qwen3.5:9b"
        assert lifecycle.memory_model.base_url == agent_model.base_url
        assert lifecycle.memory_model.timeout_seconds == agent_model.timeout_seconds
    finally:
        repo.close()
