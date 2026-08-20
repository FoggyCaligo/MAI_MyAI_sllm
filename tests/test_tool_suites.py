from __future__ import annotations

from pathlib import Path
import builtins
import zipfile

import pytest

from MK5.tools.terminal_tools import TerminalToolSuite
from MK5.tools.code_index_tools import CodeIndexToolSuite
from MK5.tools.document_tools import DocumentReadToolSuite, _looks_garbled
from MK5.tools.image_tools import ImageAnalyzeToolSuite
from MK5.tools.llm_client import ModelOutputParseError, ModelTurn, _compact_tool_result, _parse_model_turn, _require_tool_manuals, _response_schema_for_tools
from MK5.tools.tool_runtime import ToolCall, ToolDefinition
from MK5.tools.workspace_tools import WorkspaceFileToolSuite
from MK5.tools import web_search
from MK5.tools.web_search import HttpWebSearchTool, SearchHit


def test_low_level_web_tools_are_hidden_from_model() -> None:
    registry = HttpWebSearchTool().build_registry()
    visible_definitions = {definition.name: definition for definition in registry.model_definitions()}

    assert "web_research" in visible_definitions
    assert "market_snapshot" in visible_definitions
    assert "internet_search" not in visible_definitions
    assert "web_page_read" not in visible_definitions
    assert set(visible_definitions["latest_search"].input_schema["properties"]) == {"query"}
    assert set(visible_definitions["market_snapshot"].input_schema["properties"]) == {"query"}


@pytest.mark.asyncio
async def test_market_snapshot_stub() -> None:
    from MK5.tools.web_search import StubWebSearchTool

    registry = StubWebSearchTool().build_registry()
    result = await registry.run(ToolCall(tool="market_snapshot", arguments={"query": "태광"}))
    assert result["ok"] is True
    assert result["type"] == "stub_quote"
    assert result["quote"]["name"] == "태광"


def test_unconsulted_tool_call_is_replaced_with_manual_lookup() -> None:
    definitions = [
        ToolDefinition(name="terminal_command", description="terminal", input_schema={}),
        ToolDefinition(name="tool_manual", description="manual", input_schema={}),
    ]
    turn = ModelTurn(tool_calls=[
        ToolCall(tool="terminal_command", arguments={"command": "tree -L 2 MK5"}),
    ])

    guarded = _require_tool_manuals(turn, tool_definitions=definitions, tool_history=[])

    assert guarded.tool_calls == [ToolCall(tool="tool_manual", arguments={"tool": "terminal_command"})]


def test_consulted_tool_call_is_preserved() -> None:
    definitions = [
        ToolDefinition(name="terminal_command", description="terminal", input_schema={}),
        ToolDefinition(name="tool_manual", description="manual", input_schema={}),
    ]
    turn = ModelTurn(tool_calls=[
        ToolCall(tool="terminal_command", arguments={"command": "dir MK5"}),
    ])
    history = [{
        "tool": "tool_manual",
        "arguments": {"tool": "terminal_command"},
        "result": {"ok": True, "tool": "terminal_command", "input_schema": {}},
    }]

    guarded = _require_tool_manuals(turn, tool_definitions=definitions, tool_history=history)

    assert guarded is turn


def test_response_schema_restricts_tool_names_to_registry_list() -> None:
    schema = _response_schema_for_tools(["file_search", "code_index", "code_search"])

    tool_schema = schema["properties"]["tool_calls"]["items"]["properties"]["tool"]
    assert tool_schema["enum"] == ["code_index", "code_search", "file_search"]


def test_invalid_model_json_raises_specific_parse_error() -> None:
    with pytest.raises(ModelOutputParseError):
        _parse_model_turn("일반 텍스트 응답")


def test_semantically_truncated_final_answer_is_rejected_even_when_json_is_valid() -> None:
    raw = (
        '{"final_answer":"문장이 여기서(","tool_calls":[],'
        '"final_answer_kind":"answer","completion_tools":[]}'
    )

    with pytest.raises(ModelOutputParseError, match="opening bracket"):
        _parse_model_turn(raw)


def test_complete_final_answer_with_balanced_parentheses_is_accepted() -> None:
    raw = (
        '{"final_answer":"마지막 답변(요약)을 확인했습니다.","tool_calls":[],'
        '"final_answer_kind":"answer","completion_tools":[]}'
    )

    assert _parse_model_turn(raw).final_answer == "마지막 답변(요약)을 확인했습니다."


@pytest.mark.asyncio
async def test_file_tools_can_create_update_and_read(tmp_path: Path) -> None:
    suite = WorkspaceFileToolSuite(tmp_path)
    registry = suite.build_registry()

    await registry.run(ToolCall(tool="file_create", arguments={
        "path": "notes/test.txt",
        "content": "hello",
    }))
    await registry.run(ToolCall(tool="file_update", arguments={
        "path": "notes/test.txt",
        "content": "goodbye",
    }))
    result = await registry.run(ToolCall(tool="file_read", arguments={
        "path": "notes/test.txt",
    }))

    assert result["content"] == "goodbye"


@pytest.mark.asyncio
async def test_file_search_returns_workspace_relative_recursive_paths(tmp_path: Path) -> None:
    (tmp_path / "MK5" / "core").mkdir(parents=True)
    (tmp_path / "MK5" / "core" / "agent.py").write_text("# agent", encoding="utf-8")
    (tmp_path / "MK5" / "README.md").write_text("# MK5", encoding="utf-8")
    registry = WorkspaceFileToolSuite(tmp_path).build_registry()

    result = await registry.run(ToolCall(tool="file_search", arguments={
        "root": "MK5",
        "pattern": "*.py",
        "recursive": True,
    }))

    assert result["ok"] is True
    assert result["workspace_root"] == str(tmp_path.resolve())
    assert result["files"] == ["MK5/core/agent.py"]


@pytest.mark.asyncio
async def test_file_search_respects_limit_and_reports_truncation(tmp_path: Path) -> None:
    for name in ("a.py", "b.py", "c.py"):
        (tmp_path / name).write_text(name, encoding="utf-8")
    registry = WorkspaceFileToolSuite(tmp_path).build_registry()

    result = await registry.run(ToolCall(tool="file_search", arguments={
        "pattern": "*.py",
        "limit": 2,
    }))

    assert result["files"] == ["a.py", "b.py"]
    assert result["count"] == 2
    assert result["truncated"] is True


@pytest.mark.asyncio
async def test_code_index_extracts_python_structure(tmp_path: Path) -> None:
    package = tmp_path / "app"
    package.mkdir()
    (package / "server.py").write_text(
        '"""HTTP server."""\n'
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n"
        "@app.get('/health')\n"
        "async def health():\n"
        "    return {'ok': True}\n"
        "class Service:\n"
        "    def run(self, value):\n"
        "        return value\n",
        encoding="utf-8",
    )
    registry = CodeIndexToolSuite(tmp_path).build_registry()

    result = await registry.run(ToolCall(tool="code_index", arguments={"root": "app"}))

    assert result["ok"] is True
    assert result["files_indexed"] == 1
    assert result["classes"] == 1
    assert result["routes"] == 1
    assert result["key_files"] == ["app/server.py"]


@pytest.mark.asyncio
async def test_code_search_returns_relevant_symbols_without_source_body(tmp_path: Path) -> None:
    package = tmp_path / "core"
    package.mkdir()
    (package / "agent.py").write_text(
        "class AgentOrchestrator:\n"
        "    async def respond(self, user_id, message):\n"
        "        secret_body_value = 'not returned as source'\n"
        "        return secret_body_value\n",
        encoding="utf-8",
    )
    suite = CodeIndexToolSuite(tmp_path)
    registry = suite.build_registry()

    result = await registry.run(ToolCall(tool="code_search", arguments={
        "root": ".",
        "query": "AgentOrchestrator respond",
    }))

    assert result["ok"] is True
    assert result["results"][0]["path"] == "core/agent.py"
    assert result["results"][0]["classes"][0]["name"] == "AgentOrchestrator"
    assert "secret_body_value" not in str(result["results"][0])


@pytest.mark.asyncio
async def test_file_update_appends_utf8_text(tmp_path: Path) -> None:
    suite = WorkspaceFileToolSuite(tmp_path)
    registry = suite.build_registry()

    await registry.run(ToolCall(tool="file_create", arguments={
        "path": "tags.txt",
        "content": "고음\n",
    }))
    result = await registry.run(ToolCall(tool="file_update", arguments={
        "path": "tags.txt",
        "content": "\"감성\"\n\"샤워\"\n",
        "mode": "append",
    }))
    read_result = await registry.run(ToolCall(tool="file_read", arguments={
        "path": "tags.txt",
    }))

    assert result["ok"] is True
    assert read_result["content"] == "고음\n\"감성\"\n\"샤워\"\n"


@pytest.mark.asyncio
async def test_file_update_rejects_append_content_that_contains_existing_file(tmp_path: Path) -> None:
    suite = WorkspaceFileToolSuite(tmp_path)
    registry = suite.build_registry()

    original = "고음\n그루브\n\n"
    await registry.run(ToolCall(tool="file_create", arguments={
        "path": "tags.txt",
        "content": original,
    }))
    result = await registry.run(ToolCall(tool="file_update", arguments={
        "path": "tags.txt",
        "content": original + "감성 샤워",
        "mode": "append",
    }))
    read_result = await registry.run(ToolCall(tool="file_read", arguments={
        "path": "tags.txt",
    }))

    assert result["ok"] is False
    assert result["error"] == "append_content_contains_existing_file"
    assert read_result["content"] == original


@pytest.mark.asyncio
async def test_file_update_replaces_exact_utf8_text(tmp_path: Path) -> None:
    suite = WorkspaceFileToolSuite(tmp_path)
    registry = suite.build_registry()

    await registry.run(ToolCall(tool="file_create", arguments={
        "path": "tags.txt",
        "content": "\"감성\"\n\"샤워\"\n",
    }))
    result = await registry.run(ToolCall(tool="file_update", arguments={
        "path": "tags.txt",
        "old": "\"감성\"\n\"샤워\"",
        "new": "감성\n샤워",
    }))
    read_result = await registry.run(ToolCall(tool="file_read", arguments={
        "path": "tags.txt",
    }))

    assert result["ok"] is True
    assert result["replacements"] == 1
    assert read_result["content"] == "감성\n샤워\n"


@pytest.mark.asyncio
async def test_file_update_old_not_found_returns_recovery_candidates(tmp_path: Path) -> None:
    current = '<a class="download-link" href="/download/token">파일 다운로드</a>\n'
    (tmp_path / "index.html").write_text(current, encoding="utf-8")
    registry = WorkspaceFileToolSuite(tmp_path).build_registry()

    result = await registry.run(ToolCall(tool="file_update", arguments={
        "path": "index.html",
        "old": '<a href="#" class="btn btn-sm">사용자명</a>',
        "new": "",
    }))
    compact = _compact_tool_result(tool="file_update", result=result)

    assert result["ok"] is False
    assert result["error"] == "old_not_found"
    assert result["recovery"]["closest_matches"]
    assert result["recovery"]["closest_matches"][0]["line"] == 1
    assert compact["recovery"]["closest_matches"]
    assert (tmp_path / "index.html").read_text(encoding="utf-8") == current


@pytest.mark.asyncio
async def test_file_update_rejects_new_without_old_instead_of_empty_overwrite(tmp_path: Path) -> None:
    suite = WorkspaceFileToolSuite(tmp_path)
    registry = suite.build_registry()

    await registry.run(ToolCall(tool="file_create", arguments={
        "path": "tags.txt",
        "content": "고음\n감성\n샤워",
    }))
    result = await registry.run(ToolCall(tool="file_update", arguments={
        "path": "tags.txt",
        "new": "고음\n감성 샤워",
    }))
    read_result = await registry.run(ToolCall(tool="file_read", arguments={
        "path": "tags.txt",
    }))

    assert result["ok"] is False
    assert result["error"] == "invalid_arguments"
    assert read_result["content"] == "고음\n감성\n샤워"


@pytest.mark.asyncio
async def test_file_read_returns_not_found_result_instead_of_raising(tmp_path: Path) -> None:
    suite = WorkspaceFileToolSuite(tmp_path)
    registry = suite.build_registry()

    result = await registry.run(ToolCall(tool="file_read", arguments={
        "path": "architecture.md",
    }))

    assert result["ok"] is False
    assert result["error"] == "not_found"
    assert result["path"] == "architecture.md"


@pytest.mark.asyncio
async def test_file_read_rejects_binary_document_instead_of_decoding(tmp_path: Path) -> None:
    suite = WorkspaceFileToolSuite(tmp_path)
    registry = suite.build_registry()
    (tmp_path / "strategy.pdf").write_bytes(b"%PDF-1.4\n\x93binary\n")

    result = await registry.run(ToolCall(tool="file_read", arguments={
        "path": "strategy.pdf",
    }))

    assert result["ok"] is False
    assert result["error"] == "unsupported_binary_document"
    assert "document_read" in result["message"]


@pytest.mark.asyncio
async def test_file_read_points_images_to_image_analyze(tmp_path: Path) -> None:
    suite = WorkspaceFileToolSuite(tmp_path)
    registry = suite.build_registry()
    (tmp_path / "chart.jpg").write_bytes(b"\xff\xd8\xff\xe0binary")

    result = await registry.run(ToolCall(tool="file_read", arguments={
        "path": "chart.jpg",
    }))

    assert result["ok"] is False
    assert result["error"] == "unsupported_binary_document"
    assert "image_analyze" in result["message"]


@pytest.mark.asyncio
async def test_document_read_extracts_docx_text(tmp_path: Path) -> None:
    suite = DocumentReadToolSuite(tmp_path)
    registry = suite.build_registry()
    docx_path = tmp_path / "strategy.docx"
    document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>노이즈 회귀 전략</w:t></w:r></w:p>
    <w:p><w:r><w:t>코스피 환경 판단</w:t></w:r></w:p>
  </w:body>
</w:document>
"""
    with zipfile.ZipFile(docx_path, "w") as archive:
        archive.writestr("word/document.xml", document_xml)

    result = await registry.run(ToolCall(tool="document_read", arguments={
        "path": "strategy.docx",
    }))

    assert result["ok"] is True
    assert result["document_type"] == "docx"
    assert result["paragraphs"] == 2
    assert "노이즈 회귀 전략" in result["content"]
    assert "코스피 환경 판단" in result["content"]


@pytest.mark.asyncio
async def test_document_read_rejects_unsupported_extension(tmp_path: Path) -> None:
    suite = DocumentReadToolSuite(tmp_path)
    registry = suite.build_registry()
    (tmp_path / "notes.txt").write_text("hello", encoding="utf-8")

    result = await registry.run(ToolCall(tool="document_read", arguments={
        "path": "notes.txt",
    }))

    assert result["ok"] is False
    assert result["error"] == "unsupported_document_type"


@pytest.mark.asyncio
async def test_image_analyze_returns_metadata_without_vision_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("MK5.config.OLLAMA_IMAGE_MODEL_NAME", "")
    monkeypatch.setattr("MK5.config.OLLAMA_MODEL_NAME", "")
    suite = ImageAnalyzeToolSuite(tmp_path)
    registry = suite.build_registry()
    from PIL import Image

    Image.new("RGB", (32, 16), color="red").save(tmp_path / "sample.png")

    result = await registry.run(ToolCall(tool="image_analyze", arguments={
        "path": "sample.png",
    }))

    assert result["ok"] is True
    assert result["image"]["format"] == "PNG"
    assert result["image"]["width"] == 32
    assert result["image"]["height"] == 16
    assert result["description"] is None
    assert "Ollama model" in result["message"]


@pytest.mark.asyncio
async def test_image_analyze_uses_configured_vision_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("MK5.config.OLLAMA_IMAGE_MODEL_NAME", "vision-model")
    captured: dict[str, object] = {}

    async def fake_image_chat(*, image_bytes: bytes, prompt: str, model: str | None = None) -> str:
        captured["bytes"] = len(image_bytes)
        captured["prompt"] = prompt
        captured["model"] = model
        return "빨간 사각형 이미지입니다."

    monkeypatch.setattr("MK5.tools.image_tools.image_chat", fake_image_chat)
    suite = ImageAnalyzeToolSuite(tmp_path)
    registry = suite.build_registry()
    from PIL import Image

    Image.new("RGB", (8, 8), color="red").save(tmp_path / "sample.jpg")

    result = await registry.run(ToolCall(tool="image_analyze", arguments={
        "path": "sample.jpg",
        "prompt": "무엇이 보이나요?",
    }))

    assert result["ok"] is True
    assert result["vision_model_used"] == "vision-model"
    assert result["description"] == "빨간 사각형 이미지입니다."
    assert captured["model"] == "vision-model"
    assert captured["prompt"] == "무엇이 보이나요?"
    assert int(captured["bytes"]) > 0


@pytest.mark.asyncio
async def test_image_analyze_falls_back_to_default_ollama_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("MK5.config.OLLAMA_IMAGE_MODEL_NAME", "")
    monkeypatch.setattr("MK5.config.OLLAMA_MODEL_NAME", "gemma4:e4b")
    captured: dict[str, object] = {}

    async def fake_image_chat(*, image_bytes: bytes, prompt: str, model: str | None = None) -> str:
        captured["model"] = model
        return "이미지 설명"

    monkeypatch.setattr("MK5.tools.image_tools.image_chat", fake_image_chat)
    suite = ImageAnalyzeToolSuite(tmp_path)
    registry = suite.build_registry()
    from PIL import Image

    Image.new("RGB", (4, 4), color="blue").save(tmp_path / "sample.png")

    result = await registry.run(ToolCall(tool="image_analyze", arguments={
        "path": "sample.png",
    }))

    assert result["ok"] is True
    assert result["vision_model_used"] == "gemma4:e4b"
    assert result["description"] == "이미지 설명"
    assert captured["model"] == "gemma4:e4b"


@pytest.mark.asyncio
async def test_image_analyze_retries_fallback_when_primary_rejects_image_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("MK5.config.OLLAMA_IMAGE_FALLBACK_MODEL_NAME", "gemma4:12b")
    used_models: list[str | None] = []

    async def fake_image_chat(*, image_bytes: bytes, prompt: str, model: str | None = None) -> str:
        used_models.append(model)
        if model == "qwen3:8b":
            raise ValueError("Ollama rejected image request for model 'qwen3:8b'.")
        return "실현손익 +139,546원입니다."

    monkeypatch.setattr("MK5.tools.image_tools.image_chat", fake_image_chat)
    suite = ImageAnalyzeToolSuite(tmp_path)
    registry = suite.build_registry()
    from PIL import Image

    Image.new("RGB", (4, 4), color="blue").save(tmp_path / "sample.png")

    result = await registry.run(ToolCall(tool="image_analyze", arguments={
        "path": "sample.png",
        "model": "qwen3:8b",
    }))

    assert result["ok"] is True
    assert used_models == ["qwen3:8b", "gemma4:12b"]
    assert result["vision_model_used"] == "gemma4:12b"
    assert "+139,546원" in result["description"]
    assert "qwen3:8b" in result["warning"]


@pytest.mark.asyncio
async def test_image_analyze_rejects_unsupported_extension(tmp_path: Path) -> None:
    suite = ImageAnalyzeToolSuite(tmp_path)
    registry = suite.build_registry()
    (tmp_path / "notes.txt").write_text("not image", encoding="utf-8")

    result = await registry.run(ToolCall(tool="image_analyze", arguments={
        "path": "notes.txt",
    }))

    assert result["ok"] is False
    assert result["error"] == "unsupported_image_type"


def test_document_read_detects_low_quality_extracted_text() -> None:
    assert _looks_garbled("\x00\x00\x00 깨진 \x00\x00 텍스트") is True
    assert _looks_garbled("노이즈 회귀 기반 스윙 트레이딩 전략") is False

@pytest.mark.asyncio
async def test_file_read_can_access_parent_and_absolute_paths(tmp_path: Path) -> None:
    main_root = tmp_path / "main"
    sibling_root = tmp_path / "playlist2"
    main_root.mkdir()
    sibling_root.mkdir()
    (sibling_root / "tag.txt").write_text("감성\n", encoding="utf-8")
    suite = WorkspaceFileToolSuite(main_root)
    registry = suite.build_registry()

    relative_result = await registry.run(ToolCall(tool="file_read", arguments={
        "path": "../playlist2/tag.txt",
    }))
    absolute_result = await registry.run(ToolCall(tool="file_read", arguments={
        "path": str(sibling_root / "tag.txt"),
    }))

    assert relative_result["content"] == "감성\n"
    assert absolute_result["content"] == "감성\n"


@pytest.mark.asyncio
async def test_file_delete_can_delete_file(tmp_path: Path) -> None:
    suite = WorkspaceFileToolSuite(tmp_path)
    registry = suite.build_registry()

    await registry.run(ToolCall(tool="file_create", arguments={
        "path": "tags.txt",
        "content": "감성\n",
    }))
    delete_result = await registry.run(ToolCall(tool="file_delete", arguments={
        "path": "tags.txt",
    }))
    read_result = await registry.run(ToolCall(tool="file_read", arguments={
        "path": "tags.txt",
    }))

    assert delete_result["ok"] is True
    assert read_result["ok"] is False
    assert read_result["error"] == "not_found"


@pytest.mark.asyncio
async def test_terminal_tool_allows_shell_commands_without_command_blocklist(tmp_path: Path) -> None:
    suite = TerminalToolSuite(tmp_path)
    registry = suite.build_registry()

    result = await registry.run(ToolCall(tool="terminal_command", arguments={"command": "echo ok"}))

    assert result["returncode"] == 0
    assert "ok" in result["stdout"]


@pytest.mark.asyncio
async def test_terminal_tool_result_includes_cwd(tmp_path: Path) -> None:
    suite = TerminalToolSuite(tmp_path)
    registry = suite.build_registry()

    result = await registry.run(ToolCall(tool="terminal_command", arguments={"command": "pwd"}))

    assert Path(result["cwd"]) == tmp_path.resolve()


def test_model_turn_parser_rejects_plain_text_fallback() -> None:
    with pytest.raises(RuntimeError):
        _parse_model_turn("검색 결과를 바탕으로 답변합니다.")


def test_model_turn_parser_reads_completion_evidence_fields() -> None:
    turn = _parse_model_turn(
        """
        {
          "final_answer": "수정했습니다.",
          "tool_calls": [],
          "final_answer_kind": "tool_completion",
          "completion_tools": ["file_update"]
        }
        """
    )

    assert turn.final_answer == "수정했습니다."
    assert turn.final_answer_kind == "tool_completion"
    assert turn.completion_tools == ["file_update"]


@pytest.mark.asyncio
async def test_internet_search_runs_per_concept_node(monkeypatch: pytest.MonkeyPatch) -> None:
    searched: list[tuple[str, str]] = []

    async def fake_ddg(query: str) -> list[SearchHit]:
        searched.append(("ddg", query))
        return [SearchHit(title=f"{query}-ddg", url=f"https://example.com/{query}/ddg", snippet="result", source="duckduckgo")]

    async def fake_wiki(query: str, lang: str) -> list[SearchHit]:
        searched.append((f"wiki_{lang}", query))
        return [SearchHit(title=f"{query}-{lang}", url=f"https://example.com/{query}/{lang}", snippet="result", source=f"wikipedia_{lang}")]

    monkeypatch.setattr("MK5.tools.web_search._ddg_search", fake_ddg)
    monkeypatch.setattr("MK5.tools.web_search._wiki_search", fake_wiki)

    result = await HttpWebSearchTool()._run({
        "query": "파이썬 러스트 비교",
        "search_nodes": ["파이썬", "러스트"],
    })

    assert result["search_nodes"] == ["파이썬", "러스트"]
    assert {item["query_node"] for item in result["results"]} == {"파이썬", "러스트"}
    assert ("ddg", "파이썬") in searched
    assert ("wiki_ko", "러스트") in searched


@pytest.mark.asyncio
async def test_internet_search_falls_back_to_whole_query_without_node_heuristics(monkeypatch: pytest.MonkeyPatch) -> None:
    searched: list[str] = []

    async def fake_ddg(query: str) -> list[SearchHit]:
        searched.append(query)
        return [SearchHit(title="whole-query", url="https://example.com", snippet="result", source="duckduckgo")]

    async def fake_wiki(query: str, lang: str) -> list[SearchHit]:
        searched.append(query)
        return []

    monkeypatch.setattr("MK5.tools.web_search._ddg_search", fake_ddg)
    monkeypatch.setattr("MK5.tools.web_search._wiki_search", fake_wiki)

    query = "글록의 특징과 총기시장에서의 의의"
    result = await HttpWebSearchTool()._run({"query": query})

    assert result["search_nodes"] == [query]
    assert query in searched


@pytest.mark.asyncio
async def test_internet_search_keeps_up_to_eight_node_combinations(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_ddg(query: str) -> list[SearchHit]:
        return [SearchHit(title=query, url=f"https://example.com/{query}", snippet="result", source="duckduckgo")]

    async def fake_wiki(query: str, lang: str) -> list[SearchHit]:
        return []

    monkeypatch.setattr("MK5.tools.web_search._ddg_search", fake_ddg)
    monkeypatch.setattr("MK5.tools.web_search._wiki_search", fake_wiki)
    nodes = [f"node-{index}" for index in range(8)]

    result = await HttpWebSearchTool()._run({"query": "context", "search_nodes": nodes})

    assert result["search_nodes"] == nodes
    assert {item["query_node"] for item in result["results"]} == set(nodes)


@pytest.mark.asyncio
async def test_web_page_read_extracts_focus_passages(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch(url: str) -> tuple[str, str, str]:
        return (
            url,
            "text/html; charset=utf-8",
            "<html><head><title>강지</title></head><body>"
            "<p>일반 소개입니다.</p><p>방송 시작일은 2012년 5월 10일입니다.</p>"
            "<script>ignore me</script></body></html>",
        )

    monkeypatch.setattr(web_search, "_fetch_public_page", fake_fetch)
    registry = HttpWebSearchTool().build_registry()

    result = await registry.run(ToolCall(
        tool="web_page_read",
        arguments={"url": "https://example.com/kangji", "focus": ["방송 시작일"]},
    ))

    assert result["ok"] is True
    assert result["title"] == "강지"
    assert result["matched_sections"] == ["방송 시작일은 2012년 5월 10일입니다."]
    assert "ignore me" not in result["content"]


@pytest.mark.asyncio
async def test_web_research_searches_ranks_and_reads_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = HttpWebSearchTool()

    async def fake_search(query: str, *, search_nodes: list[str] | None = None):
        return ([
            SearchHit(
                title="unrelated",
                url="https://example.com/other",
                snippet="other",
                source="stub",
                query_node="broad",
            ),
            SearchHit(
                title="강지 - 나무위키",
                url="https://namu.wiki/w/강지",
                snippet="대한민국의 인터넷 방송인",
                source="stub",
                query_node="focused",
            ),
        ], [])

    async def fake_page(arguments: dict):
        return {
            "ok": True,
            "url": arguments["url"],
            "title": "강지 - 나무위키",
            "matched_sections": ["방송 시작일은 2012년 5월 10일이다."],
            "content": "방송 시작일은 2012년 5월 10일이다.",
            "truncated": False,
        }

    monkeypatch.setattr(tool, "_search_with_diagnostics", fake_search)
    monkeypatch.setattr(tool, "_run_page_read", fake_page)

    result = await tool._run_research({
        "objective": "스트리머 강지 방송 시작일 활동 연차",
        "preferred_domains": ["namu.wiki"],
    })

    assert result["status"] == "evidence_found"
    assert result["results"][0]["url"] == "https://namu.wiki/w/강지"
    assert result["evidence"][0]["matched_sections"]


@pytest.mark.asyncio
async def test_latest_search_returns_recent_news_freshness(monkeypatch: pytest.MonkeyPatch) -> None:
    searched: list[str] = []

    async def fake_ddg_news(query: str) -> list[SearchHit]:
        searched.append(f"ddg:{query}")
        return [SearchHit(title="시장 뉴스", url="https://example.com/news", snippet="오늘 코스피 상승", source="duckduckgo_news")]

    async def fake_google_news(query: str) -> list[SearchHit]:
        searched.append(f"google:{query}")
        return []

    monkeypatch.setattr("MK5.tools.web_search._ddg_news_search", fake_ddg_news)
    monkeypatch.setattr("MK5.tools.web_search._google_news_rss_search", fake_google_news)

    result = await HttpWebSearchTool()._run_latest({
        "query": "현재 한국 주식 장 상황",
        "search_nodes": ["한국 주식 장", "코스피"],
    })

    assert result["ok"] is True
    assert result["freshness"] == "recent_news"
    assert result["search_nodes"] == ["한국 주식 장", "코스피"]
    assert result["results"][0]["source"] == "duckduckgo_news"
    assert "ddg:한국 주식 장" in searched
    assert "google:코스피" in searched


@pytest.mark.asyncio
async def test_latest_search_reports_unknown_freshness_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_ddg_news(query: str) -> list[SearchHit]:
        return []

    async def fake_google_news(query: str) -> list[SearchHit]:
        return []

    monkeypatch.setattr("MK5.tools.web_search._ddg_news_search", fake_ddg_news)
    monkeypatch.setattr("MK5.tools.web_search._google_news_rss_search", fake_google_news)

    result = await HttpWebSearchTool()._run_latest({"query": "현재 한국 주식 장 상황"})

    assert result["ok"] is False
    assert result["freshness"] == "unknown"
    assert result["results"] == []


def test_duckduckgo_missing_dependency_degrades_to_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def fake_import(name: str, *args, **kwargs):
        if name == "ddgs":
            raise ModuleNotFoundError("No module named 'ddgs'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    assert web_search._ddg_search_sync("글록") == []


@pytest.mark.asyncio
async def test_wikipedia_search_strips_html_snippets(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "query": {
                    "search": [{
                        "title": "글록",
                        "snippet": "<span>글록</span> 권총",
                    }]
                }
            }

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            return None

        async def get(self, *args, **kwargs) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr(web_search.httpx, "AsyncClient", FakeClient)

    hits = await web_search._wiki_search("글록", "ko")

    assert hits[0].snippet == "글록 권총"
