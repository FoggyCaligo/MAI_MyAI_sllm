"""Preserve counts/column context before text truncation, using existing tools."""
import asyncio
import json

from mai.agent.runtime import AgentRuntime
from mai.agent.tool_results import ToolResultStore
from mai.llm.models import ModelTurn, NativeToolCall
from mai.remote.routing import register_routed_pc_tools
from mai.tools.csv_aggregate import csv_aggregate, CsvMetric
from mai.tools.filesystem import file_read, file_list, file_search, register_filesystem_read_tools
from mai.tools.registry import ToolRegistry


def _visible_page(result, limit=2048):
    serialized = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    return ToolResultStore(max_inline_chars=limit).model_view(serialized)


def test_csv_metadata_counts_records_without_inventing_a_header(tmp_path):
    source = tmp_path / "quoted.csv"
    source.write_text('"symbol,label",amount\n"A\nB",10\nC,20\n', encoding="utf-8")
    result = file_read(path=str(source), max_chars=1)
    assert result["truncated"] is True
    assert result["details"]["first_record"] == ["symbol,label", "amount"]
    assert result["details"]["row_count"] == 3  # CSV records, not physical lines.
    assert "header presence is not inferred" in result["details"]["row_count_scope"]
    aggregate = csv_aggregate(path=str(source), metrics=[
        CsvMetric(name="rows", operation="count_rows"),
        CsvMetric(name="total", operation="sum", column="amount"),
    ])
    assert aggregate["scope"]["total_data_rows"] == 2
    assert aggregate["groups"][0]["metrics"] == {"rows": "2", "total": "30"}


def test_empty_and_headerless_csv_do_not_gain_assumed_column_labels(tmp_path):
    source = tmp_path / "plain.csv"
    source.write_text("", encoding="utf-8")
    assert file_read(path=str(source))["details"]["first_record"] == []
    source.write_text("A,10\nB,20\n", encoding="utf-8")
    details = file_read(path=str(source))["details"]
    assert details["first_record"] == ["A", "10"]
    assert details["row_count"] == 2
    assert "header presence is not inferred" in details["row_count_scope"]


def test_large_collection_completeness_survives_initial_tool_page(tmp_path):
    for index in range(80):
        (tmp_path / f"record-{index:03d}.txt").write_text("", encoding="utf-8")
    for result in (
        file_list(path=str(tmp_path), max_items=40),
        file_search(root=str(tmp_path), pattern="*.txt", max_results=40),
    ):
        page = _visible_page(result)
        assert len(page) <= 2048
        assert '"returned_count":40,"total_count":null,"has_more":true,"complete":false' in page
        assert result["collection"]["complete"] is False


def test_runtime_keeps_csv_columns_and_record_scope_visible_on_first_read(tmp_path):
    source = tmp_path / "large.csv"
    source.write_text("symbol,amount\n" + "A,10\n" * 1000, encoding="utf-8")
    registry = ToolRegistry()
    register_filesystem_read_tools(registry)
    original_names = registry.names()

    def call(name, arguments):
        return ModelTurn("", "", (NativeToolCall(name, arguments),), {
            "role": "assistant", "content": "", "tool_calls": [{
                "function": {"name": name, "arguments": arguments},
            }],
        })

    class Adapter:
        def __init__(self):
            self.requests = []
            self.turns = iter([
                call("tool_manual", {"tools": ["file_read"]}),
                call("file_read", {"path": str(source)}),
                ModelTurn("Read the source.", "", (), {"role": "assistant", "content": "Read the source."}),
            ])

        async def chat(self, request):
            self.requests.append(request)
            return next(self.turns)

    adapter = Adapter()
    result = asyncio.run(AgentRuntime(
        adapter, registry, tool_result_store=ToolResultStore(max_inline_chars=2048),
    ).run_user_message("Inspect this CSV"))
    assert result.model_rounds == 3
    assert registry.names() == original_names
    page = next(message["content"] for message in adapter.requests[2].messages
                if message.get("role") == "tool" and message.get("tool_name") == "file_read")
    assert '"first_record":["symbol","amount"]' in page
    assert '"row_count":1001' in page
    assert "header presence is not inferred" in page
    assert len(page) <= 2048


def test_routed_existing_tools_preserve_summaries_and_argument_guidance(tmp_path):
    registry = ToolRegistry()
    register_routed_pc_tools(registry, owner_id="owner", cwd=str(tmp_path))
    terminal = registry.get("terminal_run")
    assert len(terminal.metadata["catalog_description"]) < len(terminal.description)
    schema = terminal.native_schema()["function"]["parameters"]
    assert schema["properties"]["is_remote"]["default"] is False
    assert "selected shell" in schema["properties"]["command"]["description"]
    assert "Format-List" in schema["properties"]["command"]["description"]
