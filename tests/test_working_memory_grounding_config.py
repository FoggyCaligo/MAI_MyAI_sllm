from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from mai.scratchpad import EvidenceKindToolAdapter, ScratchpadRegistry, TurnEvidenceRegistry
from mai.working_memory_lifecycle import WorkingMemoryLifecycle


@dataclass
class ConfigurableModel:
    configured: frozenset[str] = frozenset()

    def configure_grounding_tools(self, tool_names):
        self.configured = frozenset(tool_names)

    def structured(self, *, messages, schema):
        raise AssertionError("not used")


@dataclass
class DummyTool:
    name: str
    description: str = "dummy"
    work_kind: str = "action"

    def schema(self):
        return {"type": "object"}

    def execute(self, *, arguments, context):
        return {}


@dataclass
class DummyAttachments:
    def build(self, paths):
        return []


def test_working_memory_configures_grounding_tools_from_evidence_kind() -> None:
    model = ConfigurableModel()
    evidence = TurnEvidenceRegistry()
    scratchpads = ScratchpadRegistry(evidence=evidence)
    web_tool = EvidenceKindToolAdapter(DummyTool("market_snapshot"), "web_evidence")
    file_tool = EvidenceKindToolAdapter(DummyTool("file_read"), "file_evidence")
    delegate = SimpleNamespace(
        memory_executor=SimpleNamespace(scratchpads=None),
        work_tools=[web_tool, file_tool],
        model=model,
    )

    WorkingMemoryLifecycle(
        delegate=delegate,
        attachments=DummyAttachments(),
        evidence=evidence,
        scratchpads=scratchpads,
        memory_model=model,
    )

    assert model.configured == frozenset({"market_snapshot"})
