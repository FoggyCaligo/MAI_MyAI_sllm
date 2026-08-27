"""Provider-neutral model request/response contracts used by the MAI runtime."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


JsonObject = dict[str, Any]
Message = dict[str, Any]
ToolSchema = dict[str, Any]
ThinkSetting = bool | str


@dataclass(frozen=True, slots=True)
class ModelConfig:
    """Configuration for one Ollama-backed model adapter."""

    model: str
    host: str = "http://127.0.0.1:11434"
    think: ThinkSetting = True
    options: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ValueError("model must be a non-empty Ollama model name")
        if not self.host.strip():
            raise ValueError("host must be a non-empty Ollama host")


@dataclass(frozen=True, slots=True)
class NativeToolCall:
    """Normalized Ollama native function call."""

    name: str
    arguments: JsonObject
    index: int | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("tool call name must be non-empty")


@dataclass(frozen=True, slots=True)
class ModelTurn:
    """One assistant turn returned by the model adapter.

    `assistant_message` is deliberately retained in normalized Ollama message
    form so the Agent Runtime can append it to the next native tool round
    without reconstructing tool calls from prose.
    """

    content: str
    thinking: str
    tool_calls: tuple[NativeToolCall, ...]
    assistant_message: Message

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


@dataclass(frozen=True, slots=True)
class ChatRequest:
    """Provider-neutral input accepted by the Ollama adapter."""

    messages: Sequence[Mapping[str, Any]]
    tools: Sequence[Mapping[str, Any]] = ()
    think: ThinkSetting | None = None
    options: Mapping[str, Any] | None = None
