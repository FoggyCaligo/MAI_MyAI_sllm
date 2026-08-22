from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from mai.attachment_evidence import AttachmentEvidenceBuilder
from mai.final_memory import FinalMemoryExecutor
from mai.model import ModelContractError
from mai.scratchpad import (
    EvidenceKindToolAdapter,
    EvidenceTrackingTool,
    ScratchpadPutTool,
    ScratchpadRegistry,
    ScratchpadUpdateTool,
    TurnEvidenceRegistry,
)


@dataclass
class DummyAnalyzer:
    model: str = "vision"

    def analyze(self, *, path: Path, prompt: str) -> str:
        return f"analyzed:{path.name}:{prompt[:8]}"


@dataclass
class DummyTool:
    name: str = "dummy_tool"
    description: str = "dummy"
    work_kind: str = "action"

    def schema(self):
        return {"type": "object"}

    def execute(self, *, arguments, context):
        return {"value": arguments["value"]}


@dataclass
class CapturingWriter:
    scopes: list = None

    def __post_init__(self):
        self.scopes = [] if self.scopes is None else self.scopes

    def execute(self, *, arguments, scope):
        self.scopes.append(scope)
        return {"status": "written", "created_nodes": []}


@dataclass
class UnusedReviser:
    def execute(self, *, arguments, scope):
        raise AssertionError("reviser should not run")


def test_text_attachment_is_loaded_as_structured_evidence(tmp_path: Path) -> None:
    path = tmp_path / "note.txt"
    path.write_text("hello attachment", encoding="utf-8")
    builder = AttachmentEvidenceBuilder(analyzer=DummyAnalyzer())

    evidence = builder.build([path])

    assert evidence == [
        {
            "evidence_id": "attachment:1",
            "path": str(path.resolve()),
            "kind": "text",
            "status": "loaded",
            "content": "hello attachment",
            "truncated": False,
        }
    ]


def test_unsupported_attachment_type_is_explicit_not_guessed(tmp_path: Path) -> None:
    path = tmp_path / "data.bin"
    path.write_bytes(b"\x00\x01")
    builder = AttachmentEvidenceBuilder(analyzer=DummyAnalyzer())

    evidence = builder.build([path])[0]

    assert evidence["status"] == "unsupported_attachment_type"
    assert evidence["kind"] == "unsupported"


def test_evidence_tracking_tool_adds_turn_scoped_evidence_id() -> None:
    evidence = TurnEvidenceRegistry()
    tool = EvidenceTrackingTool(DummyTool(), evidence)
    context = SimpleNamespace(turn_id="turn-1")

    result = tool.execute(arguments={"value": 7}, context=context)

    assert result == {"value": 7, "evidence_id": "tool:1"}
    stored = evidence.require(turn_id="turn-1", evidence_id="tool:1")
    assert stored.payload["tool"] == "dummy_tool"
    assert stored.payload["result"] == {"value": 7}


def test_evidence_tracking_tool_preserves_explicit_evidence_kind() -> None:
    evidence = TurnEvidenceRegistry()
    declared = EvidenceKindToolAdapter(DummyTool(), "web_evidence")
    tool = EvidenceTrackingTool(declared, evidence)

    result = tool.execute(arguments={"value": 7}, context=SimpleNamespace(turn_id="turn-1"))

    assert result["evidence_id"] == "tool:1"
    assert result["evidence_kind"] == "web_evidence"


def test_scratchpad_rejects_unknown_evidence_source() -> None:
    evidence = TurnEvidenceRegistry()
    scratchpads = ScratchpadRegistry(evidence=evidence)
    tool = ScratchpadPutTool(scratchpads=scratchpads, evidence=evidence)
    context = SimpleNamespace(turn_id="turn-1")

    with pytest.raises(ModelContractError, match="outside current-turn evidence scope"):
        tool.execute(
            arguments={"content": "important", "source_ids": ["tool:99"]},
            context=context,
        )


def test_scratchpad_update_replaces_content_sources_and_preserves_id() -> None:
    evidence = TurnEvidenceRegistry()
    scratchpads = ScratchpadRegistry(evidence=evidence)
    evidence.register_attachment(
        turn_id="turn-1",
        item={"evidence_id": "attachment:1", "status": "loaded", "content": "first"},
    )
    evidence.register_attachment(
        turn_id="turn-1",
        item={"evidence_id": "attachment:2", "status": "loaded", "content": "second"},
    )
    created = scratchpads.put(
        turn_id="turn-1",
        content="old fact",
        source_ids=["attachment:1"],
    )
    tool = ScratchpadUpdateTool(scratchpads=scratchpads, evidence=evidence)

    result = tool.execute(
        arguments={
            "scratchpad_id": created.scratchpad_id,
            "content": "revised fact",
            "source_ids": ["attachment:2"],
        },
        context=SimpleNamespace(turn_id="turn-1"),
    )

    assert result["status"] == "updated"
    assert result["scratchpad_id"] == created.scratchpad_id
    assert result["content"] == "revised fact"
    assert result["source_ids"] == ["attachment:2"]
    assert scratchpads.get(turn_id="turn-1", scratchpad_id=created.scratchpad_id).content == "revised fact"


def test_scratchpad_update_rejects_unknown_current_turn_id() -> None:
    evidence = TurnEvidenceRegistry()
    scratchpads = ScratchpadRegistry(evidence=evidence)
    evidence.register_attachment(
        turn_id="turn-1",
        item={"evidence_id": "attachment:1", "status": "loaded", "content": "first"},
    )
    tool = ScratchpadUpdateTool(scratchpads=scratchpads, evidence=evidence)

    with pytest.raises(ModelContractError, match="outside current-turn scope"):
        tool.execute(
            arguments={
                "scratchpad_id": "scratchpad:99",
                "content": "revised fact",
                "source_ids": ["attachment:1"],
            },
            context=SimpleNamespace(turn_id="turn-1"),
        )


def test_final_memory_uses_only_selected_scratchpad_context() -> None:
    evidence = TurnEvidenceRegistry()
    scratchpads = ScratchpadRegistry(evidence=evidence)
    evidence.register_attachment(
        turn_id="turn-1",
        item={"evidence_id": "attachment:1", "status": "loaded", "content": "source"},
    )
    first = scratchpads.put(
        turn_id="turn-1",
        content="selected fact",
        source_ids=["attachment:1"],
    )
    scratchpads.put(
        turn_id="turn-1",
        content="unselected fact",
        source_ids=["attachment:1"],
    )
    writer = CapturingWriter()
    executor = FinalMemoryExecutor(writer=writer, reviser=UnusedReviser(), scratchpads=scratchpads)

    executor.execute(
        user_id="user",
        turn_id="turn-1",
        user_text="question",
        fixed_answer="answer",
        recall_result=None,
        mutations=[
            {
                "kind": "write_memory",
                "arguments": {
                    "subject": {"kind": "user"},
                    "relation": "knows",
                    "object": {"new_node": {"name": "fact"}},
                },
                "scratchpad_ids": [first.scratchpad_id],
            }
        ],
    )

    source_context = writer.scopes[0].source_context()
    assert "selected fact" in source_context
    assert "unselected fact" not in source_context
    assert "attachment:1" in source_context


def test_final_memory_rejects_unknown_scratchpad_id() -> None:
    evidence = TurnEvidenceRegistry()
    scratchpads = ScratchpadRegistry(evidence=evidence)
    executor = FinalMemoryExecutor(
        writer=CapturingWriter(),
        reviser=UnusedReviser(),
        scratchpads=scratchpads,
    )

    with pytest.raises(ModelContractError, match="outside current-turn scope"):
        executor.execute(
            user_id="user",
            turn_id="turn-1",
            user_text="question",
            fixed_answer="answer",
            recall_result=None,
            mutations=[
                {
                    "kind": "write_memory",
                    "arguments": {
                        "subject": {"kind": "user"},
                        "relation": "knows",
                        "object": {"new_node": {"name": "fact"}},
                    },
                    "scratchpad_ids": ["scratchpad:99"],
                }
            ],
        )
