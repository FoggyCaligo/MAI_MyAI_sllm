from __future__ import annotations

from typing import Any

import pytest

from MK5.core.agent.orchestrator import AgentOrchestrator, _final_answer_evidence_guard_result
from MK5.core.graph.repository import GraphRepository
from MK5.core.graph.service import GraphMemoryService
from MK5.tools.graph_tools import GraphToolSuite
from MK5.tools.document_tools import DocumentReadToolSuite
from MK5.tools.llm_client import ModelTurn
from MK5.tools.terminal_tools import TerminalToolSuite
from MK5.tools.tool_runtime import ToolCall, ToolDefinition
from MK5.tools.web_search import StubWebSearchTool
from MK5.tools.tool_runtime import ToolRegistry
from MK5.tools.workspace_tools import WorkspaceFileToolSuite


def test_tool_completion_without_names_uses_successful_substantive_tool_history() -> None:
    guard = _final_answer_evidence_guard_result(
        turn=ModelTurn(
            final_answer="검색 결과입니다.",
            final_answer_kind="tool_completion",
            completion_tools=[],
        ),
        tool_history=[{
            "tool": "web_research",
            "arguments": {"objective": "test"},
            "result": {"ok": True, "results": [{"title": "result"}]},
        }],
        rejected_final_answer="검색 결과입니다.",
    )

    assert guard is None


def test_tool_completion_without_names_is_treated_as_plain_answer_without_tool_history() -> None:
    guard = _final_answer_evidence_guard_result(
        turn=ModelTurn(
            final_answer="완료했습니다.",
            final_answer_kind="tool_completion",
            completion_tools=[],
        ),
        tool_history=[{
            "tool": "tool_manual",
            "arguments": {"tool": "web_research"},
            "result": {"ok": True},
        }],
        rejected_final_answer="완료했습니다.",
    )

    assert guard is None


class FakeToolCallingModel:
    def __init__(self) -> None:
        self._turn = 0

    async def next_turn(
        self,
        *,
        system: str,
        user_message: str,
        model: str | None,
        memory_summary: list[Any],
        tool_definitions: list[ToolDefinition],
        tool_history: list[dict[str, Any]],
    ) -> ModelTurn:
        self._turn += 1
        if self._turn == 1:
            return ModelTurn(tool_calls=[ToolCall(tool="graph_search", arguments={"query": "hello"})])
        assert tool_history
        return ModelTurn(final_answer="done")


class MemoryRecallWithoutSearchModel:
    def __init__(self) -> None:
        self.turns = 0

    async def next_turn(
        self,
        *,
        system: str,
        user_message: str,
        model: str | None,
        memory_summary: list[Any],
        tool_definitions: list[ToolDefinition],
        tool_history: list[dict[str, Any]],
    ) -> ModelTurn:
        self.turns += 1
        if self.turns == 1:
            assert memory_summary
            return ModelTurn(
                final_answer="관련 키워드는 기억합니다. 더 알려주시겠어요?",
                final_answer_kind="tool_completion",
                completion_tools=["graph_search"],
            )
        if not any(event.get("tool") == "graph_search" for event in tool_history):
            assert any(
                event.get("tool") == "execution_guard"
                and event.get("result", {}).get("error") == "completion_tool_not_run"
                for event in tool_history
            )
            return ModelTurn(tool_calls=[
                ToolCall(
                    tool="graph_search",
                    arguments={"query": "스텔라이브 아이리 칸나", "limit": 8},
                )
            ])
        return ModelTurn(
            final_answer="검색한 과거 대화의 상세 내용입니다.",
            final_answer_kind="tool_completion",
            completion_tools=["graph_search"],
        )


class FakeWebResearchModel:
    def __init__(self) -> None:
        self._turn = 0

    async def next_turn(
        self,
        *,
        system: str,
        user_message: str,
        model: str | None,
        memory_summary: list[Any],
        tool_definitions: list[ToolDefinition],
        tool_history: list[dict[str, Any]],
    ) -> ModelTurn:
        self._turn += 1
        if self._turn == 1:
            return ModelTurn(tool_calls=[ToolCall(
                tool="web_research",
                arguments={"objective": "graph memory architecture"},
            )])
        assert tool_history
        return ModelTurn(final_answer="search done")


class FakeImageAnalyzeModel:
    def __init__(self) -> None:
        self._turn = 0

    async def next_turn(
        self,
        *,
        system: str,
        user_message: str,
        model: str | None,
        memory_summary: list[Any],
        tool_definitions: list[ToolDefinition],
        tool_history: list[dict[str, Any]],
    ) -> ModelTurn:
        self._turn += 1
        if self._turn == 1:
            return ModelTurn(tool_calls=[
                ToolCall(tool="image_analyze", arguments={"path": "chart.jpg"})
            ])
        return ModelTurn(final_answer="이미지를 확인했습니다.")


class RepeatingImageAnalyzeWithFinalModel:
    def __init__(self) -> None:
        self._turn = 0

    async def next_turn(
        self,
        *,
        system: str,
        user_message: str,
        model: str | None,
        memory_summary: list[Any],
        tool_definitions: list[ToolDefinition],
        tool_history: list[dict[str, Any]],
    ) -> ModelTurn:
        self._turn += 1
        if self._turn == 1:
            return ModelTurn(tool_calls=[
                ToolCall(tool="image_analyze", arguments={"path": "chart.jpg"})
            ])
        return ModelTurn(
            final_answer="이미지를 읽었습니다.",
            tool_calls=[ToolCall(tool="image_analyze", arguments={"path": "chart.jpg"})],
        )


class ShouldNotBeCalledModel:
    async def next_turn(
        self,
        *,
        system: str,
        user_message: str,
        model: str | None,
        memory_summary: list[Any],
        tool_definitions: list[ToolDefinition],
        tool_history: list[dict[str, Any]],
    ) -> ModelTurn:
        raise AssertionError("model should not be called")


class FinalOnlyModel:
    def __init__(self) -> None:
        self.memory_summaries: list[list[Any]] = []

    async def next_turn(
        self,
        *,
        system: str,
        user_message: str,
        model: str | None,
        memory_summary: list[Any],
        tool_definitions: list[ToolDefinition],
        tool_history: list[dict[str, Any]],
    ) -> ModelTurn:
        self.memory_summaries.append(memory_summary)
        return ModelTurn(final_answer="done")


class ContextAwareFileReadModel:
    def __init__(self) -> None:
        self.last_user_message = ""
        self._turn = 0

    async def next_turn(
        self,
        *,
        system: str,
        user_message: str,
        model: str | None,
        memory_summary: list[Any],
        tool_definitions: list[ToolDefinition],
        tool_history: list[dict[str, Any]],
    ) -> ModelTurn:
        self.last_user_message = user_message
        self._turn += 1
        if self._turn == 1:
            return ModelTurn(final_answer="ready")
        if not tool_history:
            return ModelTurn(tool_calls=[
                ToolCall(tool="file_read", arguments={"path": "README.md"}),
                ToolCall(tool="file_read", arguments={"path": "MK5/README.md"}),
            ])
        return ModelTurn(final_answer="done")


class WrongDocumentReadForMarkdownModel:
    def __init__(self) -> None:
        self._turn = 0

    async def next_turn(
        self,
        *,
        system: str,
        user_message: str,
        model: str | None,
        memory_summary: list[Any],
        tool_definitions: list[ToolDefinition],
        tool_history: list[dict[str, Any]],
    ) -> ModelTurn:
        self._turn += 1
        if self._turn == 1:
            return ModelTurn(tool_calls=[
                ToolCall(tool="document_read", arguments={"path": "README.md"})
            ])
        return ModelTurn(final_answer="done")


class PreviousDialogueModel:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self._turn = 0

    async def next_turn(
        self,
        *,
        system: str,
        user_message: str,
        model: str | None,
        memory_summary: list[Any],
        tool_definitions: list[ToolDefinition],
        tool_history: list[dict[str, Any]],
    ) -> ModelTurn:
        self.messages.append(user_message)
        self._turn += 1
        if self._turn == 1:
            return ModelTurn(final_answer="graph_search 도구로 확인해보겠습니다.")
        return ModelTurn(final_answer="done")


class TerminalOnlyFileMutationModel:
    def __init__(self) -> None:
        self.saw_mutation_guard = False

    async def next_turn(
        self,
        *,
        system: str,
        user_message: str,
        model: str | None,
        memory_summary: list[Any],
        tool_definitions: list[ToolDefinition],
        tool_history: list[dict[str, Any]],
    ) -> ModelTurn:
        if any(event.get("tool") == "file_update" for event in tool_history):
            return ModelTurn(final_answer="수정했습니다.")
        if any(
            event.get("tool") == "execution_guard"
            and event.get("result", {}).get("error") == "terminal_filesystem_change_not_verified"
            for event in tool_history
        ):
            self.saw_mutation_guard = True
            return ModelTurn(tool_calls=[
                ToolCall(
                    tool="file_update",
                    arguments={
                        "path": "../playlist2/pli_file/tag.txt",
                        "old": "감성\n샤워",
                        "new": "감성 샤워",
                    },
                )
            ])
        if any(event.get("tool") == "terminal_command" for event in tool_history):
            return ModelTurn(final_answer="수정했습니다.")
        return ModelTurn(tool_calls=[
            ToolCall(
                tool="terminal_command",
                arguments={
                    "command": "echo pretend-edit > terminal-marker.txt",
                },
            )
        ])


class FollowupFileCorrectionModel:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self._turn = 0

    async def next_turn(
        self,
        *,
        system: str,
        user_message: str,
        model: str | None,
        memory_summary: list[Any],
        tool_definitions: list[ToolDefinition],
        tool_history: list[dict[str, Any]],
    ) -> ModelTurn:
        self.messages.append(user_message)
        self._turn += 1
        if self._turn == 1 or (
            tool_history
            and all(event.get("tool") in {"internet_search", "web_research"} for event in tool_history)
        ):
            return ModelTurn(tool_calls=[
                ToolCall(
                    tool="file_update",
                    arguments={
                        "path": "../playlist2/pli_file/tag.txt",
                        "content": "감성, 샤워",
                        "mode": "append",
                    },
                )
            ])
        if any(event.get("tool") == "file_update" for event in tool_history):
            return ModelTurn(final_answer="완료했습니다.")
        assert "Previous tool operation" in user_message
        assert "../playlist2/pli_file/tag.txt" in user_message
        return ModelTurn(tool_calls=[
            ToolCall(
                tool="file_update",
                arguments={
                    "path": "../playlist2/pli_file/tag.txt",
                    "old": "감성, 샤워",
                    "new": "감성 샤워",
                },
            )
        ])


class PrematureToolCompletionClaimModel:
    def __init__(self) -> None:
        self.saw_completion_guard = False

    async def next_turn(
        self,
        *,
        system: str,
        user_message: str,
        model: str | None,
        memory_summary: list[Any],
        tool_definitions: list[ToolDefinition],
        tool_history: list[dict[str, Any]],
    ) -> ModelTurn:
        if any(event.get("tool") == "file_update" for event in tool_history):
            return ModelTurn(
                final_answer="추가했습니다.",
                final_answer_kind="tool_completion",
                completion_tools=["file_update"],
            )
        if any(
            event.get("tool") == "execution_guard"
            and event.get("result", {}).get("error") == "completion_tool_not_run"
            for event in tool_history
        ):
            self.saw_completion_guard = True
            return ModelTurn(tool_calls=[
                ToolCall(
                    tool="file_update",
                    arguments={
                        "path": "../playlist2/pli_file/tag.txt",
                        "content": "감성 샤워",
                        "mode": "append",
                    },
                )
            ])
        return ModelTurn(
            final_answer="추가했습니다.",
            final_answer_kind="tool_completion",
            completion_tools=["file_update"],
        )


class EmptyAfterFileReadModel:
    def __init__(self) -> None:
        self.saw_empty_read_guard = False

    async def next_turn(
        self,
        *,
        system: str,
        user_message: str,
        model: str | None,
        memory_summary: list[Any],
        tool_definitions: list[ToolDefinition],
        tool_history: list[dict[str, Any]],
    ) -> ModelTurn:
        if any(event.get("tool") == "file_update" for event in tool_history):
            return ModelTurn(
                final_answer="추가했습니다.",
                final_answer_kind="tool_completion",
                completion_tools=["file_update"],
            )
        if any(
            event.get("tool") == "execution_guard"
            and event.get("result", {}).get("error") == "empty_turn_after_file_read"
            for event in tool_history
        ):
            self.saw_empty_read_guard = True
            return ModelTurn(tool_calls=[
                ToolCall(
                    tool="file_update",
                    arguments={
                        "path": "../playlist2/pli_file/tag.txt",
                        "content": "감성 샤워",
                        "mode": "append",
                    },
                )
            ])
        if any(event.get("tool") == "file_read" for event in tool_history):
            return ModelTurn()
        return ModelTurn(tool_calls=[
            ToolCall(
                tool="file_read",
                arguments={"path": "../playlist2/pli_file/tag.txt"},
            )
        ])


class EmptyAfterVerifiedFileUpdateModel:
    def __init__(self) -> None:
        self.saw_empty_guard_after_verification = False

    async def next_turn(
        self,
        *,
        system: str,
        user_message: str,
        model: str | None,
        memory_summary: list[Any],
        tool_definitions: list[ToolDefinition],
        tool_history: list[dict[str, Any]],
    ) -> ModelTurn:
        file_reads = [event for event in tool_history if event.get("tool") == "file_read"]
        has_update = any(event.get("tool") == "file_update" for event in tool_history)
        if any(
            event.get("tool") == "execution_guard"
            and event.get("result", {}).get("error") == "empty_turn_after_file_read"
            for event in tool_history
        ):
            self.saw_empty_guard_after_verification = True
            return ModelTurn(
                final_answer="추가했습니다.",
                final_answer_kind="tool_completion",
                completion_tools=["file_update", "file_read"],
            )
        if has_update and len(file_reads) >= 2:
            return ModelTurn()
        if has_update:
            return ModelTurn(tool_calls=[
                ToolCall(
                    tool="file_read",
                    arguments={"path": "../playlist2/pli_file/tag.txt"},
                )
            ])
        if file_reads:
            return ModelTurn(tool_calls=[
                ToolCall(
                    tool="file_update",
                    arguments={
                        "path": "../playlist2/pli_file/tag.txt",
                        "content": "감성, 샤워",
                        "mode": "append",
                    },
                )
            ])
        return ModelTurn(tool_calls=[
            ToolCall(
                tool="file_read",
                arguments={"path": "../playlist2/pli_file/tag.txt"},
            )
        ])


class InvalidFileUpdateThenRetryModel:
    def __init__(self) -> None:
        self.saw_failed_mutation_guard = False

    async def next_turn(
        self,
        *,
        system: str,
        user_message: str,
        model: str | None,
        memory_summary: list[Any],
        tool_definitions: list[ToolDefinition],
        tool_history: list[dict[str, Any]],
    ) -> ModelTurn:
        if any(
            event.get("tool") == "file_update"
            and event.get("result", {}).get("ok") is True
            for event in tool_history
        ):
            return ModelTurn(
                final_answer="콤마를 제거했습니다.",
                final_answer_kind="tool_completion",
                completion_tools=["file_update"],
            )
        if any(
            event.get("tool") == "execution_guard"
            and event.get("result", {}).get("error") == "empty_turn_after_failed_file_mutation"
            for event in tool_history
        ):
            self.saw_failed_mutation_guard = True
            return ModelTurn(tool_calls=[
                ToolCall(
                    tool="file_update",
                    arguments={
                        "path": "../playlist2/pli_file/tag.txt",
                        "old": "감성, 샤워",
                        "new": "감성 샤워",
                    },
                )
            ])
        if any(
            event.get("tool") == "file_update"
            and event.get("result", {}).get("ok") is not True
            for event in tool_history
        ):
            return ModelTurn()
        if any(event.get("tool") == "file_read" for event in tool_history):
            return ModelTurn(tool_calls=[
                ToolCall(
                    tool="file_update",
                    arguments={
                        "path": "../playlist2/pli_file/tag.txt",
                        "old": "감성, 샤워",
                        "content": "감성 샤워",
                        "mode": "overwrite",
                    },
                )
            ])
        return ModelTurn(tool_calls=[
            ToolCall(
                tool="file_read",
                arguments={"path": "../playlist2/pli_file/tag.txt"},
            )
        ])


class PreviousToolContextModel:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def next_turn(
        self,
        *,
        system: str,
        user_message: str,
        model: str | None,
        memory_summary: list[Any],
        tool_definitions: list[ToolDefinition],
        tool_history: list[dict[str, Any]],
    ) -> ModelTurn:
        self.messages.append(user_message)
        if "Current user message:\n../playlist2/pli" in user_message:
            if not any(event.get("tool") == "terminal_command" for event in tool_history):
                return ModelTurn(tool_calls=[
                    ToolCall(
                        tool="terminal_command",
                        arguments={"command": "Get-ChildItem -Path ../playlist2/pli/*.mp3"},
                    )
                ])
            return ModelTurn(final_answer="mp3 검색은 실패했습니다.")
        if "Current user message:\n.mp3가 아니라 .opus" in user_message:
            return ModelTurn(final_answer=".opus 확장자로 이해했습니다.")
        if tool_history:
            return ModelTurn(final_answer="opus 검색을 진행했습니다.")
        assert "Previous tool operation" in user_message
        assert "*.mp3" in user_message
        assert "../playlist2/pli" in user_message
        assert ".opus" in user_message
        return ModelTurn(tool_calls=[
            ToolCall(
                tool="terminal_command",
                arguments={"command": "Get-ChildItem -Path ../playlist2/pli/*.opus"},
            )
        ])


class MalformedThenScriptModel:
    def __init__(self) -> None:
        self.saw_parse_guard = False

    async def next_turn(
        self,
        *,
        system: str,
        user_message: str,
        model: str | None,
        memory_summary: list[Any],
        tool_definitions: list[ToolDefinition],
        tool_history: list[dict[str, Any]],
    ) -> ModelTurn:
        if any(event.get("tool") == "terminal_command" for event in tool_history):
            return ModelTurn(final_answer="스크립트를 실행했습니다.")
        if any(event.get("tool") == "file_create" for event in tool_history):
            return ModelTurn(tool_calls=[
                ToolCall(
                    tool="terminal_command",
                    arguments={"command": "python artists.py"},
                )
            ])
        if any(
            event.get("tool") == "execution_guard"
            and event.get("result", {}).get("error") == "model_output_parse_failed"
            for event in tool_history
        ):
            self.saw_parse_guard = True
            return ModelTurn(tool_calls=[
                ToolCall(
                    tool="file_create",
                    arguments={
                        "path": "artists.py",
                        "content": "print('artist')\n",
                    },
                )
            ])
        raise RuntimeError("Model response must be valid JSON with final_answer and tool_calls: Unterminated string")


class EmptyInitialThenToolModel:
    def __init__(self) -> None:
        self.saw_empty_initial_guard = False

    async def next_turn(
        self,
        *,
        system: str,
        user_message: str,
        model: str | None,
        memory_summary: list[Any],
        tool_definitions: list[ToolDefinition],
        tool_history: list[dict[str, Any]],
    ) -> ModelTurn:
        if any(event.get("tool") == "terminal_command" for event in tool_history):
            return ModelTurn(final_answer="실행했습니다.")
        if any(
            event.get("tool") == "execution_guard"
            and event.get("result", {}).get("error") in {"empty_initial_turn", "empty_turn_after_tool"}
            for event in tool_history
        ):
            self.saw_empty_initial_guard = True
            return ModelTurn(tool_calls=[
                ToolCall(
                    tool="terminal_command",
                    arguments={"command": "python -c \"print('ok')\""},
                )
            ])
        return ModelTurn()


class MixedFinalAndToolCallModel:
    async def next_turn(
        self,
        *,
        system: str,
        user_message: str,
        model: str | None,
        memory_summary: list[Any],
        tool_definitions: list[ToolDefinition],
        tool_history: list[dict[str, Any]],
    ) -> ModelTurn:
        if any(event.get("tool") == "file_update" for event in tool_history):
            return ModelTurn(
                final_answer="수정했습니다.",
                final_answer_kind="tool_completion",
                completion_tools=["file_update"],
            )
        return ModelTurn(
            final_answer="수정했습니다.",
            tool_calls=[
                ToolCall(
                    tool="file_update",
                    arguments={
                        "path": "../playlist2/pli_file/tag.txt",
                        "old": "감성, 샤워",
                        "new": "감성 샤워",
                    },
                )
            ],
            final_answer_kind="tool_completion",
            completion_tools=["file_update"],
        )


class LocalToolBlockedThenTerminalModel:
    def __init__(self) -> None:
        self.saw_blocked_guard = False

    async def next_turn(
        self,
        *,
        system: str,
        user_message: str,
        model: str | None,
        memory_summary: list[Any],
        tool_definitions: list[ToolDefinition],
        tool_history: list[dict[str, Any]],
    ) -> ModelTurn:
        if any(event.get("tool") == "terminal_command" for event in tool_history):
            return ModelTurn(final_answer="커밋 목록을 확인했습니다.")
        if any(
            event.get("tool") == "execution_guard"
            and event.get("result", {}).get("error") == "local_tool_blocked_without_attempt"
            for event in tool_history
        ):
            self.saw_blocked_guard = True
            return ModelTurn(tool_calls=[
                ToolCall(tool="terminal_command", arguments={"command": "git log --oneline -5"})
            ])
        return ModelTurn(
            final_answer="로컬 작업을 진행할 수 없습니다.",
            final_answer_kind="blocked",
        )


class FinalAnswerAsToolModel:
    def __init__(self) -> None:
        self.saw_unknown_tool_guard = False

    async def next_turn(
        self,
        *,
        system: str,
        user_message: str,
        model: str | None,
        memory_summary: list[Any],
        tool_definitions: list[ToolDefinition],
        tool_history: list[dict[str, Any]],
    ) -> ModelTurn:
        if any(
            event.get("tool") == "execution_guard"
            and event.get("result", {}).get("error") == "unknown_tool_call"
            and event.get("result", {}).get("unknown_tool") == "final_answer"
            for event in tool_history
        ):
            self.saw_unknown_tool_guard = True
            return ModelTurn(final_answer="다시 설명드립니다.")
        return ModelTurn(
            tool_calls=[
                ToolCall(
                    tool="final_answer",
                    arguments={"text": "요약은 다음과 같습니다."},
                )
            ]
        )


class CapturingWebSearchTool:
    def build_registry(self) -> ToolRegistry:
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="internet_search",
                description="capture search arguments",
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}, "search_nodes": {"type": "array"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
                model_visible=False,
            ),
            self._run,
        )
        registry.register(
            ToolDefinition(
                name="web_research",
                description="capture research arguments",
                input_schema={
                    "type": "object",
                    "properties": {
                        "objective": {"type": "string"},
                    },
                    "required": ["objective"],
                    "additionalProperties": False,
                },
            ),
            self._run_research,
        )
        return registry

    async def _run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        nodes = [str(node) for node in arguments.get("search_nodes", [])]
        return {
            "query": arguments.get("query"),
            "search_nodes": nodes,
            "results": [
                {
                    "title": f"{node} result",
                    "url": f"https://example.com/{node}",
                    "snippet": "result",
                    "source": "stub",
                    "query_node": node,
                }
                for node in nodes
            ],
            "source_errors": [],
        }

    async def _run_research(self, arguments: dict[str, Any]) -> dict[str, Any]:
        objective = str(arguments.get("objective") or "")
        results = [
            {
                "title": f"{objective} result",
                "url": "https://example.com/research",
                "snippet": "result",
                "source": "stub",
                "query_node": objective,
            }
        ] if objective else []
        return {
            "ok": bool(results),
            "objective": objective,
            "queries": [objective],
            "status": "snippets_only" if results else "no_results",
            "results": results,
            "evidence": [],
            "source_errors": [],
            "page_errors": [],
        }


@pytest.mark.asyncio
async def test_orchestrator_runs_tool_then_returns_answer() -> None:
    repo = GraphRepository(":memory:")
    memory = GraphMemoryService(repo)
    memory.record_user_utterance(user_id="alice", text="hello world", session_id="s1")
    graph_tools = GraphToolSuite(memory)
    orchestrator = AgentOrchestrator(
        memory_service=memory,
        graph_tools=graph_tools,
        chat_model=FakeToolCallingModel(),
        web_search=StubWebSearchTool(),
    )

    result = await orchestrator.respond(user_id="alice", message="hello", model=None, session_id="s1")

    assert result.text == "done"
    assert "graph_search" in result.used_tools
    assert result.tool_events
    repo.close()


@pytest.mark.asyncio
async def test_orchestrator_forces_graph_search_before_detailed_memory_recall_answer() -> None:
    repo = GraphRepository(":memory:")
    memory = GraphMemoryService(repo)
    memory.record_user_utterance(
        user_id="신재용",
        text="스텔라이브와 아이리 칸나의 활동에 관해 길게 대화했다.",
        session_id="s1",
    )
    chat_model = MemoryRecallWithoutSearchModel()
    orchestrator = AgentOrchestrator(
        memory_service=memory,
        graph_tools=GraphToolSuite(memory),
        chat_model=chat_model,
        web_search=StubWebSearchTool(),
    )

    result = await orchestrator.respond(
        user_id="신재용",
        message="이전에 스텔라이브와 아이리 칸나에 대해 어떤 대화를 했는지 상세히 확인해줘.",
        session_id="s1",
    )

    assert result.text == "검색한 과거 대화의 상세 내용입니다."
    assert "graph_search" in result.used_tools
    graph_event = next(event for event in result.tool_events if event["tool"] == "graph_search")
    current_utterance = next(
        node
        for node in repo.all_nodes()
        if node.node_type == "utterance"
        and node.labels
        and node.labels[0].startswith("이전에 스텔라이브")
    )
    assert current_utterance.node_id in graph_event["arguments"]["exclude_node_ids"]
    assert all(
        item["focus"]["node_id"] != current_utterance.node_id
        for item in graph_event["result"]["results"]
    )
    repo.close()


@pytest.mark.asyncio
async def test_graph_tools_force_the_current_request_user_id() -> None:
    repo = GraphRepository(":memory:")
    memory = GraphMemoryService(repo)
    memory.record_user_utterance(user_id="alice", text="Alice secret memory.", session_id="s1")
    bob_utterance_id = memory.record_user_utterance(user_id="bob", text="Search memory.", session_id="s1")
    orchestrator = AgentOrchestrator(
        memory_service=memory,
        graph_tools=GraphToolSuite(memory),
        chat_model=FakeToolCallingModel(),
        web_search=StubWebSearchTool(),
    )

    event = await orchestrator._run_tool_call(
        ToolCall(
            tool="graph_search",
            arguments={"user_id": "alice", "query": "Alice secret memory", "limit": 8},
        ),
        user_id="bob",
        utterance_id=bob_utterance_id,
    )

    assert event["arguments"]["user_id"] == "bob"
    assert not any(
        item["focus"]["node_type"] in {"fact", "utterance"}
        for item in event["result"]["results"]
    )

    memory.record_user_utterance(user_id="bob", text="I use JavaScript.", session_id="s1")
    bob_fact = next(
        node
        for node in repo.all_nodes()
        if node.node_type == "fact" and node.payload.get("user_id") == "bob"
    )
    correction_event = await orchestrator._run_tool_call(
        ToolCall(
            tool="record_memory_correction",
            arguments={
                "user_id": "alice",
                "previous_fact_id": bob_fact.node_id,
                "replacement_text": "I use TypeScript.",
            },
        ),
        user_id="bob",
        utterance_id=bob_utterance_id,
    )

    assert correction_event["arguments"]["user_id"] == "bob"
    assert correction_event["result"]["replacement_fact_id"]
    repo.close()


@pytest.mark.asyncio
async def test_orchestrator_persists_search_results_after_tool_call() -> None:
    repo = GraphRepository(":memory:")
    memory = GraphMemoryService(repo)
    graph_tools = GraphToolSuite(memory)
    orchestrator = AgentOrchestrator(
        memory_service=memory,
        graph_tools=graph_tools,
        chat_model=FakeWebResearchModel(),
        web_search=StubWebSearchTool(),
    )

    result = await orchestrator.respond(user_id="alice", message="search graph memory", model=None, session_id="s1")

    assert result.text == "search done"
    assert "web_research" in result.used_tools
    persisted = memory.graph_search(user_id="alice", query="stub-result", limit=8)
    assert any(item["focus"]["node_type"] == "search_result" for item in persisted)
    repo.close()


@pytest.mark.asyncio
async def test_orchestrator_does_not_auto_search_from_graph_nodes() -> None:
    repo = GraphRepository(":memory:")
    memory = GraphMemoryService(repo)
    graph_tools = GraphToolSuite(memory)
    orchestrator = AgentOrchestrator(
        memory_service=memory,
        graph_tools=graph_tools,
        chat_model=FinalOnlyModel(),
        web_search=CapturingWebSearchTool(),
    )

    result = await orchestrator.respond(
        user_id="alice",
        message="Glock features and market significance",
        model=None,
        session_id="s1",
    )

    assert result.text == "done"
    assert not any(event["tool"] == "web_research" for event in result.tool_events)
    repo.close()


@pytest.mark.asyncio
async def test_orchestrator_pushes_relevant_memory_summary_into_model() -> None:
    repo = GraphRepository(":memory:")
    memory = GraphMemoryService(repo)
    memory.record_user_utterance(user_id="alice", text="I enjoy TypeScript.", session_id="s1")
    graph_tools = GraphToolSuite(memory)
    chat_model = FinalOnlyModel()
    orchestrator = AgentOrchestrator(
        memory_service=memory,
        graph_tools=graph_tools,
        chat_model=chat_model,
        web_search=CapturingWebSearchTool(),
    )

    result = await orchestrator.respond(
        user_id="alice",
        message="TypeScript 기억 있어?",
        model=None,
        session_id="s1",
    )

    assert result.text == "done"
    assert chat_model.memory_summaries
    assert isinstance(chat_model.memory_summaries[-1][0], dict)
    assert "score" in chat_model.memory_summaries[-1][0]
    repo.close()


@pytest.mark.asyncio
async def test_orchestrator_passes_previous_dialogue_so_model_can_read_files(tmp_path) -> None:
    (tmp_path / "README.md").write_text("Root project", encoding="utf-8")
    (tmp_path / "MK5").mkdir()
    (tmp_path / "MK5" / "README.md").write_text("MK5 project", encoding="utf-8")

    repo = GraphRepository(":memory:")
    memory = GraphMemoryService(repo)
    graph_tools = GraphToolSuite(memory)
    chat_model = ContextAwareFileReadModel()
    orchestrator = AgentOrchestrator(
        memory_service=memory,
        graph_tools=graph_tools,
        chat_model=chat_model,
        web_search=CapturingWebSearchTool(),
    )
    orchestrator.register_tool_registry(WorkspaceFileToolSuite(tmp_path).build_registry())

    await orchestrator.respond(
        user_id="alice",
        message="루트의 README.md 파일과, MK5 폴더 안의 README.md 파일을 우선 봐줘.",
        model=None,
        session_id="s1",
    )
    result = await orchestrator.respond(
        user_id="alice",
        message="응. 읽어봐줘.",
        model=None,
        session_id="s1",
    )

    file_events = [event for event in result.tool_events if event["tool"] == "file_read"]
    assert [event["arguments"]["path"] for event in file_events] == ["README.md", "MK5/README.md"]
    assert file_events[0]["result"]["content"] == "Root project"
    assert file_events[1]["result"]["content"] == "MK5 project"
    assert "루트의 README.md" in chat_model.last_user_message
    assert "응. 읽어봐줘." in chat_model.last_user_message
    repo.close()


@pytest.mark.asyncio
async def test_orchestrator_passes_previous_dialogue_turn_for_confirmation() -> None:
    repo = GraphRepository(":memory:")
    memory = GraphMemoryService(repo)
    graph_tools = GraphToolSuite(memory)
    chat_model = PreviousDialogueModel()
    orchestrator = AgentOrchestrator(
        memory_service=memory,
        graph_tools=graph_tools,
        chat_model=chat_model,
        web_search=CapturingWebSearchTool(),
    )

    await orchestrator.respond(
        user_id="alice",
        message="나에 대해 기억하니?",
        model=None,
        session_id="s1",
    )
    await orchestrator.respond(
        user_id="alice",
        message="응. 그 도구로 한번 진행해봐.",
        model=None,
        session_id="s1",
    )

    assert "Previous dialogue turn" in chat_model.messages[-1]
    assert "Assistant: graph_search 도구로 확인해보겠습니다." in chat_model.messages[-1]
    assert "응. 그 도구로 한번 진행해봐." in chat_model.messages[-1]
    repo.close()


@pytest.mark.asyncio
async def test_orchestrator_accumulates_recent_dialogue_like_mk1_history() -> None:
    repo = GraphRepository(":memory:")
    memory = GraphMemoryService(repo)
    graph_tools = GraphToolSuite(memory)
    chat_model = PreviousDialogueModel()
    orchestrator = AgentOrchestrator(
        memory_service=memory,
        graph_tools=graph_tools,
        chat_model=chat_model,
        web_search=CapturingWebSearchTool(),
    )

    await orchestrator.respond(
        user_id="alice",
        message="첫 번째 문맥: playlist2 폴더를 봤어.",
        model=None,
        session_id="s1",
    )
    await orchestrator.respond(
        user_id="alice",
        message="두 번째 문맥: 파일은 opus였어.",
        model=None,
        session_id="s1",
    )
    await orchestrator.respond(
        user_id="alice",
        message="응. 진행해줘.",
        model=None,
        session_id="s1",
    )

    latest = chat_model.messages[-1]
    assert "Previous dialogue turn" in latest
    assert "첫 번째 문맥: playlist2 폴더를 봤어." in latest
    assert "두 번째 문맥: 파일은 opus였어." in latest
    assert "응. 진행해줘." in latest
    repo.close()


@pytest.mark.asyncio
async def test_orchestrator_rejects_terminal_only_file_mutation_completion(tmp_path) -> None:
    workspace = tmp_path / "MACHI"
    target_dir = tmp_path / "playlist2" / "pli_file"
    workspace.mkdir()
    target_dir.mkdir(parents=True)
    target_file = target_dir / "tag.txt"
    target_file.write_text("고음\n감성\n샤워", encoding="utf-8")

    repo = GraphRepository(":memory:")
    memory = GraphMemoryService(repo)
    graph_tools = GraphToolSuite(memory)
    chat_model = TerminalOnlyFileMutationModel()
    orchestrator = AgentOrchestrator(
        memory_service=memory,
        graph_tools=graph_tools,
        chat_model=chat_model,
        web_search=CapturingWebSearchTool(),
    )
    orchestrator.register_tool_registry(WorkspaceFileToolSuite(workspace).build_registry())
    orchestrator.register_tool_registry(TerminalToolSuite(workspace).build_registry())

    result = await orchestrator.respond(
        user_id="alice",
        message="현재 루트에서 한 단계 위의 playlist2/pli_file/tag.txt에서 감성과 샤워 사이의 개행을 없애서 한 줄로 만들어줘.",
        model=None,
        session_id="s1",
    )

    assert result.text == "수정했습니다."
    assert chat_model.saw_mutation_guard is True
    assert any(event["tool"] == "terminal_command" for event in result.tool_events)
    assert any(event["tool"] == "file_update" for event in result.tool_events)
    assert target_file.read_text(encoding="utf-8") == "고음\n감성 샤워"
    repo.close()


@pytest.mark.asyncio
async def test_orchestrator_passes_previous_file_operation_for_followup_correction(tmp_path) -> None:
    workspace = tmp_path / "MACHI"
    target_dir = tmp_path / "playlist2" / "pli_file"
    workspace.mkdir()
    target_dir.mkdir(parents=True)
    target_file = target_dir / "tag.txt"
    target_file.write_text("", encoding="utf-8")

    repo = GraphRepository(":memory:")
    memory = GraphMemoryService(repo)
    graph_tools = GraphToolSuite(memory)
    chat_model = FollowupFileCorrectionModel()
    orchestrator = AgentOrchestrator(
        memory_service=memory,
        graph_tools=graph_tools,
        chat_model=chat_model,
        web_search=CapturingWebSearchTool(),
    )
    orchestrator.register_tool_registry(WorkspaceFileToolSuite(workspace).build_registry())

    await orchestrator.respond(
        user_id="alice",
        message="tag.txt에 감성, 샤워를 추가해줘.",
        model=None,
        session_id="s1",
    )
    result = await orchestrator.respond(
        user_id="alice",
        message="콤마는 없애줘.",
        model=None,
        session_id="s1",
    )

    assert result.text == "완료했습니다."
    assert "Previous file operation" not in chat_model.messages[-1]
    assert "Previous tool operation" in chat_model.messages[-1]
    assert target_file.read_text(encoding="utf-8") == "감성 샤워"
    repo.close()


@pytest.mark.asyncio
async def test_orchestrator_rejects_tool_completion_claim_without_tool_evidence(tmp_path) -> None:
    workspace = tmp_path / "MACHI"
    target_dir = tmp_path / "playlist2" / "pli_file"
    workspace.mkdir()
    target_dir.mkdir(parents=True)
    target_file = target_dir / "tag.txt"
    target_file.write_text("고음\n", encoding="utf-8")

    repo = GraphRepository(":memory:")
    memory = GraphMemoryService(repo)
    graph_tools = GraphToolSuite(memory)
    chat_model = PrematureToolCompletionClaimModel()
    orchestrator = AgentOrchestrator(
        memory_service=memory,
        graph_tools=graph_tools,
        chat_model=chat_model,
        web_search=CapturingWebSearchTool(),
    )
    orchestrator.register_tool_registry(WorkspaceFileToolSuite(workspace).build_registry())

    result = await orchestrator.respond(
        user_id="alice",
        message="../playlist2/pli_file/tag.txt 맨 마지막 줄에 감성 샤워를 추가해줘.",
        model=None,
        session_id="s1",
    )

    assert result.text == "추가했습니다."
    assert chat_model.saw_completion_guard is True
    assert any(event["tool"] == "file_update" for event in result.tool_events)
    assert target_file.read_text(encoding="utf-8") == "고음\n감성 샤워"
    repo.close()


@pytest.mark.asyncio
async def test_orchestrator_continues_after_empty_turn_following_file_read(tmp_path) -> None:
    workspace = tmp_path / "MACHI"
    target_dir = tmp_path / "playlist2" / "pli_file"
    workspace.mkdir()
    target_dir.mkdir(parents=True)
    target_file = target_dir / "tag.txt"
    target_file.write_text("고음\n", encoding="utf-8")

    repo = GraphRepository(":memory:")
    memory = GraphMemoryService(repo)
    graph_tools = GraphToolSuite(memory)
    chat_model = EmptyAfterFileReadModel()
    orchestrator = AgentOrchestrator(
        memory_service=memory,
        graph_tools=graph_tools,
        chat_model=chat_model,
        web_search=CapturingWebSearchTool(),
    )
    orchestrator.register_tool_registry(WorkspaceFileToolSuite(workspace).build_registry())

    result = await orchestrator.respond(
        user_id="alice",
        message="../playlist2/pli_file/tag.txt 맨 마지막 줄에 감성 샤워를 추가해줘.",
        model=None,
        session_id="s1",
    )

    assert result.text == "추가했습니다."
    assert chat_model.saw_empty_read_guard is True
    file_tools = [event["tool"] for event in result.tool_events if event["tool"].startswith("file_")]
    assert file_tools == ["file_read", "file_update"]
    assert target_file.read_text(encoding="utf-8") == "고음\n감성 샤워"
    repo.close()


@pytest.mark.asyncio
async def test_orchestrator_allows_llm_final_after_verified_file_update_empty_turn(tmp_path) -> None:
    workspace = tmp_path / "MACHI"
    target_dir = tmp_path / "playlist2" / "pli_file"
    workspace.mkdir()
    target_dir.mkdir(parents=True)
    target_file = target_dir / "tag.txt"
    target_file.write_text("고음\n", encoding="utf-8")

    repo = GraphRepository(":memory:")
    memory = GraphMemoryService(repo)
    graph_tools = GraphToolSuite(memory)
    chat_model = EmptyAfterVerifiedFileUpdateModel()
    orchestrator = AgentOrchestrator(
        memory_service=memory,
        graph_tools=graph_tools,
        chat_model=chat_model,
        web_search=CapturingWebSearchTool(),
    )
    orchestrator.register_tool_registry(WorkspaceFileToolSuite(workspace).build_registry())

    result = await orchestrator.respond(
        user_id="alice",
        message="../playlist2/pli_file/tag.txt 맨 마지막 줄에 감성, 샤워를 한 줄로 추가해줘.",
        model=None,
        session_id="s1",
    )

    assert result.text == "추가했습니다."
    assert chat_model.saw_empty_guard_after_verification is True
    file_tools = [event["tool"] for event in result.tool_events if event["tool"].startswith("file_")]
    assert file_tools == ["file_read", "file_update", "file_read"]
    assert target_file.read_text(encoding="utf-8") == "고음\n감성, 샤워"
    repo.close()


@pytest.mark.asyncio
async def test_orchestrator_guides_retry_after_invalid_file_update_arguments(tmp_path) -> None:
    workspace = tmp_path / "MACHI"
    target_dir = tmp_path / "playlist2" / "pli_file"
    workspace.mkdir()
    target_dir.mkdir(parents=True)
    target_file = target_dir / "tag.txt"
    target_file.write_text("고음\n감성, 샤워", encoding="utf-8")

    repo = GraphRepository(":memory:")
    memory = GraphMemoryService(repo)
    graph_tools = GraphToolSuite(memory)
    chat_model = InvalidFileUpdateThenRetryModel()
    orchestrator = AgentOrchestrator(
        memory_service=memory,
        graph_tools=graph_tools,
        chat_model=chat_model,
        web_search=CapturingWebSearchTool(),
    )
    orchestrator.register_tool_registry(WorkspaceFileToolSuite(workspace).build_registry())

    result = await orchestrator.respond(
        user_id="alice",
        message="../playlist2/pli_file/tag.txt의 마지막 줄에서 콤마를 지워줘.",
        model=None,
        session_id="s1",
    )

    assert result.text == "콤마를 제거했습니다."
    assert chat_model.saw_failed_mutation_guard is True
    file_updates = [event for event in result.tool_events if event["tool"] == "file_update"]
    assert file_updates[0]["result"]["ok"] is False
    assert file_updates[1]["result"]["ok"] is True
    assert target_file.read_text(encoding="utf-8") == "고음\n감성 샤워"
    repo.close()


@pytest.mark.asyncio
async def test_orchestrator_preserves_previous_tool_context_across_correction_turn(tmp_path) -> None:
    workspace = tmp_path / "MACHI"
    target_dir = tmp_path / "playlist2" / "pli"
    workspace.mkdir()
    target_dir.mkdir(parents=True)
    (target_dir / "스텔 리제 - a.opus").write_text("", encoding="utf-8")

    repo = GraphRepository(":memory:")
    memory = GraphMemoryService(repo)
    graph_tools = GraphToolSuite(memory)
    chat_model = PreviousToolContextModel()
    orchestrator = AgentOrchestrator(
        memory_service=memory,
        graph_tools=graph_tools,
        chat_model=chat_model,
        web_search=CapturingWebSearchTool(),
    )
    orchestrator.register_tool_registry(TerminalToolSuite(workspace).build_registry())

    await orchestrator.respond(
        user_id="alice",
        message="../playlist2/pli",
        model=None,
        session_id="s1",
    )
    await orchestrator.respond(
        user_id="alice",
        message=".mp3가 아니라 .opus 형태로 저장되어 있어서 그래.",
        model=None,
        session_id="s1",
    )
    result = await orchestrator.respond(
        user_id="alice",
        message="응. 진행해줘.",
        model=None,
        session_id="s1",
    )

    assert result.text == "opus 검색을 진행했습니다."
    terminal_events = [event for event in result.tool_events if event["tool"] == "terminal_command"]
    assert terminal_events[-1]["arguments"]["command"] == "Get-ChildItem -Path ../playlist2/pli/*.opus"
    repo.close()


@pytest.mark.asyncio
async def test_orchestrator_recovers_from_malformed_model_json_for_script_task(tmp_path) -> None:
    repo = GraphRepository(":memory:")
    memory = GraphMemoryService(repo)
    graph_tools = GraphToolSuite(memory)
    chat_model = MalformedThenScriptModel()
    orchestrator = AgentOrchestrator(
        memory_service=memory,
        graph_tools=graph_tools,
        chat_model=chat_model,
        web_search=CapturingWebSearchTool(),
    )
    orchestrator.register_tool_registry(WorkspaceFileToolSuite(tmp_path).build_registry())
    orchestrator.register_tool_registry(TerminalToolSuite(tmp_path).build_registry())

    result = await orchestrator.respond(
        user_id="alice",
        message="파이썬 스크립트 파일을 새로 만들어서 실행해줘.",
        model=None,
        session_id="s1",
    )

    assert result.text == "스크립트를 실행했습니다."
    assert chat_model.saw_parse_guard is True
    assert (tmp_path / "artists.py").read_text(encoding="utf-8") == "print('artist')\n"
    assert [event["tool"] for event in result.tool_events if event["tool"] != "web_research"] == [
        "file_create",
        "terminal_command",
    ]
    repo.close()


@pytest.mark.asyncio
async def test_orchestrator_recovers_from_empty_initial_model_turn(tmp_path) -> None:
    repo = GraphRepository(":memory:")
    memory = GraphMemoryService(repo)
    graph_tools = GraphToolSuite(memory)
    chat_model = EmptyInitialThenToolModel()
    orchestrator = AgentOrchestrator(
        memory_service=memory,
        graph_tools=graph_tools,
        chat_model=chat_model,
        web_search=CapturingWebSearchTool(),
    )
    orchestrator.register_tool_registry(TerminalToolSuite(tmp_path).build_registry())

    result = await orchestrator.respond(
        user_id="alice",
        message="스크립트를 실행해줘.",
        model=None,
        session_id="s1",
    )

    assert result.text == "실행했습니다."
    assert chat_model.saw_empty_initial_guard is True
    assert any(event["tool"] == "terminal_command" for event in result.tool_events)
    repo.close()


@pytest.mark.asyncio
async def test_orchestrator_rejects_local_tool_blocked_before_terminal_attempt(tmp_path) -> None:
    repo = GraphRepository(":memory:")
    memory = GraphMemoryService(repo)
    graph_tools = GraphToolSuite(memory)
    chat_model = LocalToolBlockedThenTerminalModel()
    orchestrator = AgentOrchestrator(
        memory_service=memory,
        graph_tools=graph_tools,
        chat_model=chat_model,
        web_search=CapturingWebSearchTool(),
    )
    orchestrator.register_tool_registry(TerminalToolSuite(tmp_path).build_registry())

    result = await orchestrator.respond(
        user_id="alice",
        message="git 커밋 목록을 터미널로 확인해줘.",
        model=None,
        session_id="s1",
    )

    assert result.text == "커밋 목록을 확인했습니다."
    assert chat_model.saw_blocked_guard is True
    assert any(event["tool"] == "terminal_command" for event in result.tool_events)
    repo.close()


@pytest.mark.asyncio
async def test_orchestrator_runs_tool_calls_before_mixed_final_answer(tmp_path) -> None:
    workspace = tmp_path / "MACHI"
    target_dir = tmp_path / "playlist2" / "pli_file"
    workspace.mkdir()
    target_dir.mkdir(parents=True)
    target_file = target_dir / "tag.txt"
    target_file.write_text("감성, 샤워", encoding="utf-8")

    repo = GraphRepository(":memory:")
    memory = GraphMemoryService(repo)
    graph_tools = GraphToolSuite(memory)
    orchestrator = AgentOrchestrator(
        memory_service=memory,
        graph_tools=graph_tools,
        chat_model=MixedFinalAndToolCallModel(),
        web_search=CapturingWebSearchTool(),
    )
    orchestrator.register_tool_registry(WorkspaceFileToolSuite(workspace).build_registry())

    result = await orchestrator.respond(
        user_id="alice",
        message="../playlist2/pli_file/tag.txt에서 콤마를 지워줘.",
        model=None,
        session_id="s1",
    )

    assert result.text == "수정했습니다."
    assert any(event["tool"] == "file_update" for event in result.tool_events)
    assert target_file.read_text(encoding="utf-8") == "감성 샤워"
    repo.close()


@pytest.mark.asyncio
async def test_orchestrator_passes_selected_image_model_to_image_analyze() -> None:
    captured_arguments: list[dict[str, Any]] = []

    async def fake_image_analyze(arguments: dict[str, Any]) -> dict[str, Any]:
        captured_arguments.append(dict(arguments))
        return {
            "ok": True,
            "path": arguments.get("path"),
            "image": {"format": "JPEG", "width": 1, "height": 1, "mode": "RGB", "frames": 1},
            "vision_model_used": arguments.get("model"),
            "description": "이미지 설명",
        }

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="image_analyze",
            description="fake image analyzer",
            input_schema={"type": "object"},
        ),
        fake_image_analyze,
    )

    repo = GraphRepository(":memory:")
    memory = GraphMemoryService(repo)
    graph_tools = GraphToolSuite(memory)
    orchestrator = AgentOrchestrator(
        memory_service=memory,
        graph_tools=graph_tools,
        chat_model=FakeImageAnalyzeModel(),
        web_search=CapturingWebSearchTool(),
    )
    orchestrator.register_tool_registry(registry)

    result = await orchestrator.respond(
        user_id="alice",
        message="chart.jpg를 봐줘.",
        model="gemma4:e4b",
        image_model="gemma4:12b",
        session_id="s1",
    )

    assert result.text == "이미지를 확인했습니다."
    assert captured_arguments == [
        {"path": "chart.jpg", "model": "gemma4:12b"},
        {"path": "chart.jpg", "model": "gemma4:12b"},
    ]
    image_event = next(event for event in result.tool_events if event["tool"] == "image_analyze")
    assert image_event["arguments"]["model"] == "gemma4:12b"
    repo.close()


@pytest.mark.asyncio
async def test_orchestrator_uses_final_when_mixed_turn_repeats_successful_image_tool() -> None:
    calls: list[dict[str, Any]] = []

    async def fake_image_analyze(arguments: dict[str, Any]) -> dict[str, Any]:
        calls.append(dict(arguments))
        return {
            "ok": True,
            "path": arguments.get("path"),
            "image": {"format": "JPEG", "width": 1, "height": 1, "mode": "RGB", "frames": 1},
            "vision_model_used": arguments.get("model"),
            "description": "이미지 설명",
        }

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="image_analyze",
            description="fake image analyzer",
            input_schema={"type": "object"},
        ),
        fake_image_analyze,
    )

    repo = GraphRepository(":memory:")
    memory = GraphMemoryService(repo)
    graph_tools = GraphToolSuite(memory)
    orchestrator = AgentOrchestrator(
        memory_service=memory,
        graph_tools=graph_tools,
        chat_model=RepeatingImageAnalyzeWithFinalModel(),
        web_search=CapturingWebSearchTool(),
    )
    orchestrator.register_tool_registry(registry)

    result = await orchestrator.respond(
        user_id="alice",
        message="chart.jpg를 봐줘.",
        model="gemma4:e4b",
        session_id="s1",
    )

    assert result.text == "이미지를 읽었습니다."
    assert len(calls) == 2
    assert [event["tool"] for event in result.tool_events if event["tool"] == "image_analyze"] == [
        "image_analyze",
        "image_analyze",
    ]
    repo.close()


@pytest.mark.asyncio
async def test_orchestrator_auto_analyzes_mentioned_image_path_and_returns_description() -> None:
    calls: list[dict[str, Any]] = []

    async def fake_image_analyze(arguments: dict[str, Any]) -> dict[str, Any]:
        calls.append(dict(arguments))
        return {
            "ok": True,
            "path": arguments.get("path"),
            "image": {"format": "JPEG", "width": 1080, "height": 2340, "mode": "RGB", "frames": 1},
            "vision_model_used": arguments.get("model"),
            "description": "실현손익 +139,546원, 수익률 +2.88%입니다.",
        }

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="image_analyze",
            description="fake image analyzer",
            input_schema={"type": "object"},
        ),
        fake_image_analyze,
    )

    repo = GraphRepository(":memory:")
    memory = GraphMemoryService(repo)
    graph_tools = GraphToolSuite(memory)
    orchestrator = AgentOrchestrator(
        memory_service=memory,
        graph_tools=graph_tools,
        chat_model=FinalOnlyModel(),
        web_search=CapturingWebSearchTool(),
    )
    orchestrator.register_tool_registry(registry)

    result = await orchestrator.respond(
        user_id="alice",
        message="8월_수익.jpg를 한번 봐줘.",
        model="qwen3:8b",
        image_model="gemma4:12b",
        session_id="s1",
    )

    assert result.text == "done"
    assert calls == [{"path": "8월_수익.jpg", "model": "gemma4:12b"}]
    assert result.tool_events[0]["tool"] == "image_analyze"
    repo.close()


@pytest.mark.asyncio
async def test_orchestrator_limits_and_deduplicates_auto_attachment_analysis() -> None:
    calls: list[dict[str, Any]] = []

    async def fake_image_analyze(arguments: dict[str, Any]) -> dict[str, Any]:
        calls.append(dict(arguments))
        return {
            "ok": True,
            "path": arguments.get("path"),
            "image": {"format": "JPEG", "width": 1, "height": 1, "mode": "RGB", "frames": 1},
            "vision_model_used": arguments.get("model"),
            "description": "이미지 설명",
        }

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="image_analyze",
            description="fake image analyzer",
            input_schema={"type": "object"},
        ),
        fake_image_analyze,
    )

    repo = GraphRepository(":memory:")
    memory = GraphMemoryService(repo)
    graph_tools = GraphToolSuite(memory)
    orchestrator = AgentOrchestrator(
        memory_service=memory,
        graph_tools=graph_tools,
        chat_model=FinalOnlyModel(),
        web_search=CapturingWebSearchTool(),
    )
    orchestrator.register_tool_registry(registry)
    message = (
        "첨부를 봐줘.\n\n[첨부 파일]\n"
        "- a.jpg: .mk5_uploads/a.jpg\n"
        "- b.jpg: .mk5_uploads/b.jpg\n"
        "- c.jpg: .mk5_uploads/c.jpg\n"
        "- d.jpg: .mk5_uploads/d.jpg"
    )

    await orchestrator.respond(user_id="alice", message=message, model="gemma4:e4b", session_id="s1")
    await orchestrator.respond(user_id="alice", message=message, model="gemma4:e4b", session_id="s1")

    assert [call["path"] for call in calls] == [
        ".mk5_uploads/a.jpg",
        ".mk5_uploads/b.jpg",
        ".mk5_uploads/c.jpg",
        ".mk5_uploads/d.jpg",
    ]
    repo.close()


@pytest.mark.asyncio
async def test_orchestrator_recovers_when_model_calls_final_answer_as_tool() -> None:
    repo = GraphRepository(":memory:")
    memory = GraphMemoryService(repo)
    graph_tools = GraphToolSuite(memory)
    chat_model = FinalAnswerAsToolModel()
    orchestrator = AgentOrchestrator(
        memory_service=memory,
        graph_tools=graph_tools,
        chat_model=chat_model,
        web_search=CapturingWebSearchTool(),
    )

    result = await orchestrator.respond(
        user_id="alice",
        message="각 MK 안의 readme를 보고 요약해줘.",
        model=None,
        session_id="s1",
    )

    assert result.text == "다시 설명드립니다."
    assert chat_model.saw_unknown_tool_guard is True
    assert not any(event["tool"] == "final_answer" for event in result.tool_events)
    repo.close()


def test_memory_local_activation_carries_previous_non_overlapping_nodes() -> None:
    repo = GraphRepository(":memory:")
    memory = GraphMemoryService(repo)
    first_utterance_id = memory.record_user_utterance(
        user_id="alice",
        text="playlist2 폴더의 tag.txt를 확인했다.",
        session_id="s1",
    )
    first_activation = memory.local_activation_node_ids_for_utterance(
        user_id="alice",
        utterance_id=first_utterance_id,
    )

    second_utterance_id = memory.record_user_utterance(
        user_id="alice",
        text="다음 작업으로 넘어가자.",
        session_id="s1",
    )
    second_activation = memory.local_activation_node_ids_for_utterance(
        user_id="alice",
        utterance_id=second_utterance_id,
        previous_activation_node_ids=first_activation,
    )

    assert second_utterance_id in second_activation
    assert first_activation - {second_utterance_id}
    assert (first_activation - {second_utterance_id}).issubset(second_activation)
    repo.close()


def test_memory_activation_weights_decay_previous_non_overlapping_nodes() -> None:
    repo = GraphRepository(":memory:")
    memory = GraphMemoryService(repo)
    first_utterance_id = memory.record_user_utterance(
        user_id="alice",
        text="playlist2 폴더의 tag.txt를 확인했다.",
        session_id="s1",
    )
    first_activation = memory.local_activation_node_ids_for_utterance(
        user_id="alice",
        utterance_id=first_utterance_id,
    )
    second_utterance_id = memory.record_user_utterance(
        user_id="alice",
        text="sllm 프로젝트를 다시 확인하자.",
        session_id="s1",
    )

    weights = memory.local_activation_node_weights_for_utterance(
        user_id="alice",
        utterance_id=second_utterance_id,
        previous_activation_node_ids=first_activation,
        previous_weight=0.5,
    )
    second_current = memory.local_activation_node_ids_for_utterance(
        user_id="alice",
        utterance_id=second_utterance_id,
    )
    previous_only = first_activation - second_current

    assert previous_only
    assert all(weights[node_id] == 1.0 for node_id in second_current)
    assert all(weights[node_id] == 0.5 for node_id in previous_only)
    repo.close()


@pytest.mark.asyncio
async def test_orchestrator_redirects_markdown_document_read_to_file_read(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Hello\n\n내용 테스트", encoding="utf-8")
    repo = GraphRepository(":memory:")
    service = GraphMemoryService(repo)
    model = WrongDocumentReadForMarkdownModel()
    orchestrator = AgentOrchestrator(
        memory_service=service,
        graph_tools=GraphToolSuite(service),
        chat_model=model,
        web_search=StubWebSearchTool(),
    )
    orchestrator.register_tool_registry(WorkspaceFileToolSuite(tmp_path).build_registry())
    orchestrator.register_tool_registry(DocumentReadToolSuite(tmp_path).build_registry())

    result = await orchestrator.respond(user_id="alice", session_id="s1", message="README.md 읽어줘")

    assert any(event["tool"] == "file_read" and event["result"]["ok"] for event in result.tool_events)
    assert not any(event["tool"] == "document_read" for event in result.tool_events)
    repo.close()


@pytest.mark.asyncio
async def test_orchestrator_auto_reads_mentioned_markdown_path(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Hello\n\n내용 테스트", encoding="utf-8")
    repo = GraphRepository(":memory:")
    service = GraphMemoryService(repo)
    model = FinalOnlyModel()
    orchestrator = AgentOrchestrator(
        memory_service=service,
        graph_tools=GraphToolSuite(service),
        chat_model=model,
        web_search=StubWebSearchTool(),
    )
    orchestrator.register_tool_registry(WorkspaceFileToolSuite(tmp_path).build_registry())

    result = await orchestrator.respond(user_id="alice", session_id="s1", message="./README.md")

    assert any(event["tool"] == "file_read" and event["result"]["ok"] for event in result.tool_events)
    repo.close()


@pytest.mark.asyncio
async def test_orchestrator_stops_after_repeated_model_parse_failures(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Hello\n\n내용 테스트", encoding="utf-8")

    class ParseFailingModel:
        def __init__(self) -> None:
            self.calls = 0

        async def next_turn(
            self,
            *,
            system: str,
            user_message: str,
            model: str | None,
            memory_summary: list[Any],
            tool_definitions: list[ToolDefinition],
            tool_history: list[dict[str, Any]],
        ) -> ModelTurn:
            self.calls += 1
            raise RuntimeError("Model response must be JSON with final_answer and tool_calls.")

    repo = GraphRepository(":memory:")
    service = GraphMemoryService(repo)
    model = ParseFailingModel()
    orchestrator = AgentOrchestrator(
        memory_service=service,
        graph_tools=GraphToolSuite(service),
        chat_model=model,
        web_search=StubWebSearchTool(),
    )
    orchestrator.register_tool_registry(WorkspaceFileToolSuite(tmp_path).build_registry())

    result = await orchestrator.respond(user_id="alice", session_id="s1", message="./README.md")

    assert model.calls == 3
    assert "JSON 형식" in result.text
    assert "README.md" in result.text
    repo.close()


@pytest.mark.asyncio
async def test_orchestrator_stops_after_repeated_unknown_tool_calls() -> None:
    class UnknownToolModel:
        def __init__(self) -> None:
            self.calls = 0

        async def next_turn(
            self,
            *,
            system: str,
            user_message: str,
            model: str | None,
            memory_summary: list[Any],
            tool_definitions: list[ToolDefinition],
            tool_history: list[dict[str, Any]],
        ) -> ModelTurn:
            self.calls += 1
            return ModelTurn(tool_calls=[
                ToolCall(tool="text_graph.process", arguments={})
            ])

    repo = GraphRepository(":memory:")
    service = GraphMemoryService(repo)
    model = UnknownToolModel()
    orchestrator = AgentOrchestrator(
        memory_service=service,
        graph_tools=GraphToolSuite(service),
        chat_model=model,
        web_search=StubWebSearchTool(),
    )

    result = await orchestrator.respond(user_id="alice", session_id="s1", message="text_graph.py 설명해줘")

    assert model.calls == 2
    assert "사용할 수 없는 도구" in result.text
    assert "text_graph.process" in result.text
    repo.close()


@pytest.mark.asyncio
async def test_orchestrator_runs_final_synthesis_after_identical_tool_loop_stagnates() -> None:
    class ToolLoopThenSynthesisModel:
        def __init__(self) -> None:
            self.synthesis_called = False

        async def next_turn(
            self,
            *,
            system: str,
            user_message: str,
            model: str | None,
            memory_summary: list[Any],
            tool_definitions: list[ToolDefinition],
            tool_history: list[dict[str, Any]],
        ) -> ModelTurn:
            if not tool_definitions:
                self.synthesis_called = True
                return ModelTurn(final_answer="수집한 결과를 종합했습니다.")
            return ModelTurn(tool_calls=[
                ToolCall(tool="graph_search", arguments={"query": "Machi", "limit": 1})
            ])

    repo = GraphRepository(":memory:")
    service = GraphMemoryService(repo)
    chat_model = ToolLoopThenSynthesisModel()
    orchestrator = AgentOrchestrator(
        memory_service=service,
        graph_tools=GraphToolSuite(service),
        chat_model=chat_model,
        web_search=StubWebSearchTool(),
    )

    result = await orchestrator.respond(
        user_id="alice",
        session_id="s1",
        message="Machi 코드를 전체적으로 이해해줘.",
    )

    assert chat_model.synthesis_called is True
    assert result.text == "수집한 결과를 종합했습니다."
    repo.close()

