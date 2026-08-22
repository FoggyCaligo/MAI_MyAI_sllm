from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mai.agent import AgentLifecycle, WorkContext
from mai.web_tools import MarketProviderSettings, MarketSnapshotTool


@dataclass
class ScriptedModel:
    actions: list[dict[str, Any]]
    schemas: list[dict[str, Any]] = field(default_factory=list)

    def structured(self, *, messages: list[dict[str, str]], schema: dict[str, Any]) -> dict[str, Any]:
        self.schemas.append(schema)
        return self.actions.pop(0)


class EmptyDiscovery:
    def node_lookup(self, *, user_id: str, queries: list[str]) -> dict[str, Any]:
        return {"matches": []}


class EmptyRecall:
    def recall_one_depth(self, *, user_id: str, focus_node_id: int) -> dict[str, Any]:
        return {"nodes": [], "edges": [], "origin_path": {"nodes": [], "edges": []}}


class NoScratchpadMemoryExecutor:
    pass


class FakeMarketProvider:
    def lookup(self, *, query: str, provider_scope: str, limit: int) -> list[dict[str, Any]]:
        return []

    def snapshot(self, *, provider_symbol: str, provider_scope: str) -> dict[str, Any]:
        return {"provider_symbol": provider_symbol}


def test_market_snapshot_keeps_union_inside_arguments_for_tool_manual() -> None:
    tool = MarketSnapshotTool(
        providers={"fake": FakeMarketProvider()},
        settings=MarketProviderSettings(
            kr_equity="fake",
            global_equity="fake",
            index="fake",
            fx="fake",
        ),
    )
    schema = tool.schema()

    assert schema["properties"]["tool"]["const"] == "market_snapshot"
    arguments = schema["properties"]["arguments"]
    assert "oneOf" in arguments
    operations = {
        variant["properties"]["operation"]["const"]
        for variant in arguments["oneOf"]
    }
    assert operations == {"lookup", "snapshot"}

    model = ScriptedModel(
        actions=[
            {"action": "tool", "tool": "tool_manual", "arguments": {"tool": "market_snapshot"}},
            {"action": "answer", "outcome": "completed", "content": "done"},
        ]
    )
    lifecycle = AgentLifecycle(
        repository=None,  # type: ignore[arg-type]
        model=model,
        discovery=EmptyDiscovery(),  # type: ignore[arg-type]
        recall=EmptyRecall(),  # type: ignore[arg-type]
        memory_executor=NoScratchpadMemoryExecutor(),  # type: ignore[arg-type]
        work_tools=[tool],
    )

    answer, events = lifecycle._run_agent_phase(
        context=WorkContext(user_id="u", turn_id="t", user_text="삼성전자 시세를 알려줘"),
        candidate_ids=set(),
        recall_results=[],
    )

    assert answer == "done"
    assert events[0]["tool"] == "tool_manual"
    assert events[0]["result"]["input_schema"] == arguments
