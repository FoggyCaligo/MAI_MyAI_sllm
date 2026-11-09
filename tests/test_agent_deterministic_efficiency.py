"""No-LLM regression coverage for MAI deterministic long-task efficiency."""
from __future__ import annotations

import asyncio
import logging
from pydantic import BaseModel, ConfigDict

from mai.agent.guards import AgentGuard
from mai.agent.runtime import AgentRuntime
from mai.agent.tool_results import ToolResultStore
from mai.llm.models import ModelTurn, NativeToolCall
from mai.tools.registry import ToolRegistry


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str


class _Adapter:
    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []

    async def chat(self, request):
        self.requests.append(request)
        return self.turns.pop(0)


def _turn(*calls, content=""):
    items = tuple(NativeToolCall(name=name, arguments=args) for name, args in calls)
    return ModelTurn(
        content=content, thinking="", tool_calls=items,
        assistant_message={"role": "assistant", "content": content},
    )


def test_related_failures_warn_without_blocking_changed_commands():
    guard = AgentGuard()
    command = {"command": "schtasks /query /fo LIST", "is_remote": False}
    assert guard.after_similar_failure("terminal_run", command, ok=False, error_type="TerminalTimeoutError") is None
    notice = guard.after_similar_failure(
        "terminal_run", {"command": "schtasks /query /fo CSV"}, ok=False,
        error_type="TerminalTimeoutError",
    )
    assert notice and "Related tool failures" in notice
    assert guard.after_similar_failure(
        "terminal_run", {"command": "Get-ScheduledTask"}, ok=True,
        error_type=None,
    ) is None


def test_full_file_evidence_kept_after_compaction():
    store = ToolResultStore(max_inline_chars=4000)
    original = "line of factual evidence\n" * 300
    view = store.file_model_views(original, tool_name="file_read")
    assert view.compact_history_content is not None
    import json
    compact = json.loads(view.compact_history_content)
    assert compact["source_excerpt_only"]
    result = store._results[compact["full_original_result_id"]]
    assert result.content == original
    assert "tool_result_read" == compact["read_full_with"]


def test_read_reuse_avoids_duplicate_io_and_model_rounds_do_not_increase(monkeypatch, caplog):
    monkeypatch.setenv("AGENT_EVIDENCE_REUSE_ENABLED", "true")
    monkeypatch.setenv("AGENT_PROGRESS_SUMMARY_ENABLED", "true")
    calls = []
    registry = ToolRegistry()
    registry.add(
        name="file_read", description="Read test", input_model=_Input,
        handler=lambda path: (calls.append(path), {"value": path})[1],
    )
    read = ("file_read", {"path": "example.txt"})
    adapter = _Adapter([
        _turn(("tool_manual", {"tools": ["file_read"]})),
        _turn(read), _turn(read),
        _turn(content="done"),
    ])
    runtime = AgentRuntime(adapter, registry, tool_result_store=ToolResultStore(max_inline_chars=4000))
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        result = asyncio.run(runtime.run_user_message("Read file twice"))
    assert result.content == "done"
    assert calls == ["example.txt"]
    assert len(adapter.requests) == 4
    assert len(result.tool_executions) == 3
    assert "MAI read cache hit" in caplog.text
    assert "MAI round timing" in caplog.text
    assert any(
        "Deterministic progress ledger" in str(message.get("content", ""))
        for message in adapter.requests[-1].messages
    )


def test_reuse_disabled_does_not_suppress_reads(monkeypatch):
    monkeypatch.setenv("AGENT_EVIDENCE_REUSE_ENABLED", "false")
    calls = []
    registry = ToolRegistry()
    registry.add(
        name="file_read", description="Read test", input_model=_Input,
        handler=lambda path: (calls.append(path), path)[1],
    )
    read = ("file_read", {"path": "example.txt"})
    adapter = _Adapter([
        _turn(("tool_manual", {"tools": ["file_read"]})),
        _turn(read), _turn(read), _turn(content="done"),
    ])
    runtime = AgentRuntime(adapter, registry, tool_result_store=ToolResultStore(max_inline_chars=4000))
    asyncio.run(runtime.run_user_message("Read file twice"))
    assert calls == ["example.txt", "example.txt"]


class _WriteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    content: str


def test_file_write_invalidates_read_cache(monkeypatch):
    monkeypatch.setenv("AGENT_EVIDENCE_REUSE_ENABLED", "true")
    state = {"content": "before"}
    read_calls = []
    registry = ToolRegistry()
    registry.add(
        name="file_read", description="Read test", input_model=_Input,
        handler=lambda path: (read_calls.append(path), state["content"])[1],
    )
    registry.add(
        name="file_write", description="Write test", input_model=_WriteInput,
        handler=lambda path, content: state.update(content=content) or "saved",
    )
    read = ("file_read", {"path": "sample.txt"})
    write = ("file_write", {"path": "sample.txt", "content": "after"})
    adapter = _Adapter([
        _turn(("tool_manual", {"tools": ["file_read", "file_write"]})),
        _turn(read),
        _turn(write),
        _turn(read),
        _turn(content="done"),
    ])
    runtime = AgentRuntime(adapter, registry, tool_result_store=ToolResultStore(max_inline_chars=4000))
    result = asyncio.run(runtime.run_user_message("Read then modify then reread"))
    assert result.content == "done"
    assert read_calls == ["sample.txt", "sample.txt"]
    assert [item.audit_content for item in result.tool_executions if item.name == "file_read"] == [
        '"before"', '"after"',
    ]
