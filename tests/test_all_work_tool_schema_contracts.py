from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mai.agent import AgentLifecycle, WorkContext
from mai.code_search_tool import build_code_tools
from mai.document_tools import build_document_image_tools
from mai.file_mutation_tools import DownloadGrantStore, build_file_mutation_tools
from mai.file_tools import build_file_tools
from mai.scratchpad import ScratchpadPutTool, ScratchpadRegistry, ScratchpadUpdateTool, TurnEvidenceRegistry
from mai.terminal_tool import build_terminal_tools
from mai.web_tools import MarketProviderSettings, build_web_market_tools


@dataclass
class FakeImageAnalyzer:
    model: str = "fake-vision"

    def analyze(self, *, path: Path, prompt: str) -> str:
        return "unused"


class FakeSearchProvider:
    def latest(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        return []

    def web(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        return []

    def read_page(self, url: str) -> dict[str, Any]:
        raise AssertionError("schema audit must not perform network reads")


class FakeMarketProvider:
    def lookup(self, *, query: str, provider_scope: str, limit: int) -> list[dict[str, Any]]:
        return []

    def snapshot(self, *, provider_symbol: str, provider_scope: str) -> dict[str, Any]:
        return {}


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


def _assert_common_work_tool_schema(*, tool_name: str, schema: dict[str, Any]) -> None:
    assert schema.get("type") == "object", f"{tool_name}: top-level schema must be an object"
    properties = schema.get("properties")
    assert isinstance(properties, dict), f"{tool_name}: top-level properties are required"
    assert properties.get("action") == {"const": "tool"}, f"{tool_name}: action must be const=tool"
    assert properties.get("tool") == {"const": tool_name}, f"{tool_name}: tool const must match tool.name"
    arguments = properties.get("arguments")
    assert isinstance(arguments, dict), f"{tool_name}: arguments schema must be an object"
    required = schema.get("required")
    assert isinstance(required, list), f"{tool_name}: top-level required list is required"
    assert {"action", "tool", "arguments"}.issubset(required), (
        f"{tool_name}: action/tool/arguments must all be required"
    )


def _all_registered_owner_work_tools(tmp_path: Path) -> list[Any]:
    owner_id = "owner"
    evidence = TurnEvidenceRegistry()
    scratchpads = ScratchpadRegistry(evidence=evidence)
    market_settings = MarketProviderSettings(
        kr_equity="fake",
        global_equity="fake",
        index="fake",
        fx="fake",
    )
    return [
        *build_file_tools(owner_id=owner_id, default_root=tmp_path),
        *build_file_mutation_tools(
            owner_id=owner_id,
            grants=DownloadGrantStore(),
            default_root=tmp_path,
        ),
        *build_document_image_tools(
            owner_id=owner_id,
            analyzer=FakeImageAnalyzer(),
            default_root=tmp_path,
        ),
        *build_terminal_tools(owner_id=owner_id),
        *build_code_tools(owner_id=owner_id, default_root=tmp_path),
        *build_web_market_tools(
            search_provider=FakeSearchProvider(),
            market_providers={"fake": FakeMarketProvider()},
            market_settings=market_settings,
        ),
        ScratchpadPutTool(scratchpads=scratchpads, evidence=evidence),
        ScratchpadUpdateTool(scratchpads=scratchpads, evidence=evidence),
    ]


def test_every_registered_owner_work_tool_uses_common_lazy_manual_envelope(tmp_path: Path) -> None:
    tools = _all_registered_owner_work_tools(tmp_path)
    expected_names = {
        "file_tree",
        "file_search",
        "file_text_search",
        "file_read",
        "file_create",
        "file_update",
        "file_delete",
        "file_download_link",
        "document_read",
        "image_analyze",
        "terminal_command",
        "code_index",
        "code_search",
        "latest_search",
        "web_research",
        "market_snapshot",
        "scratchpad_put",
        "scratchpad_update",
    }

    assert {tool.name for tool in tools} == expected_names
    assert len(tools) == len(expected_names)
    for tool in tools:
        _assert_common_work_tool_schema(tool_name=tool.name, schema=tool.schema())


def test_every_context_dependent_work_tool_schema_keeps_common_envelope(tmp_path: Path) -> None:
    tools = _all_registered_owner_work_tools(tmp_path)
    established_paths = {
        str((tmp_path / "note.txt").resolve()),
        str((tmp_path / "manual.pdf").resolve()),
        str((tmp_path / "image.png").resolve()),
    }

    checked: set[str] = set()
    for tool in tools:
        builder = getattr(tool, "schema_for_paths", None)
        if not callable(builder):
            continue
        schema = builder(established_paths)
        if schema is None:
            continue
        _assert_common_work_tool_schema(tool_name=tool.name, schema=schema)
        checked.add(tool.name)

    assert checked == {
        "file_read",
        "file_update",
        "file_delete",
        "file_download_link",
        "document_read",
        "image_analyze",
    }


def test_every_registered_owner_work_tool_can_open_lazy_manual(tmp_path: Path) -> None:
    for tool in _all_registered_owner_work_tools(tmp_path):
        model = ScriptedModel(
            actions=[
                {"action": "tool", "tool": "tool_manual", "arguments": {"tool": tool.name}},
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
            context=WorkContext(user_id="owner", turn_id=f"manual-{tool.name}", user_text="inspect tool manual"),
            candidate_ids=set(),
            recall_results=[],
        )

        assert answer == "done"
        assert events[0]["tool"] == "tool_manual"
        assert events[0]["result"]["tool"] == tool.name
        assert events[0]["result"]["input_schema"] == tool.schema()["properties"]["arguments"]


def test_market_snapshot_keeps_operation_union_inside_arguments(tmp_path: Path) -> None:
    tools = {tool.name: tool for tool in _all_registered_owner_work_tools(tmp_path)}
    schema = tools["market_snapshot"].schema()
    arguments = schema["properties"]["arguments"]
    variants = arguments.get("oneOf")

    assert isinstance(variants, list)
    operations = {
        variant["properties"]["operation"]["const"]
        for variant in variants
    }
    assert operations == {"lookup", "snapshot"}
