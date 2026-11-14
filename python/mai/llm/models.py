"""Provider-neutral model request/response contracts used by the MAI runtime."""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import Any, Mapping, Sequence, Callable


def model_generation_limit(name: str, default: int) -> int:
    value = int(os.environ.get(name, str(default)))
    if value <= 0:
        raise ValueError('output token limit must be positive')
    return value


JsonObject = dict[str, Any]
Message = dict[str, Any]
ToolSchema = dict[str, Any]
ThinkSetting = bool | str


def _request_timeout_seconds_from_env() -> float:
    return float(os.environ.get("MODEL_REQUEST_TIMEOUT_SECONDS", "500"))


@dataclass(frozen=True, slots=True)
class ModelConfig:
    """Configuration for one llama.cpp-backed model adapter."""

    model: str
    host: str = "http://127.0.0.1:8080"
    think: ThinkSetting = True
    options: Mapping[str, Any] = field(default_factory=dict)
    max_output_tokens: int | None = None
    request_timeout_seconds: float = field(default_factory=_request_timeout_seconds_from_env)

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ValueError("model must be a non-empty model name")
        if not self.host.strip():
            raise ValueError("host must be a non-empty inference host")
        if self.request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be positive")
        if self.max_output_tokens is not None and (type(self.max_output_tokens) is not int or self.max_output_tokens <= 0):
            raise ValueError("max_output_tokens must be a positive integer")


@dataclass(frozen=True, slots=True)
class NativeToolCall:
    """Normalized native function call."""

    name: str
    arguments: JsonObject
    index: int | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("tool call name must be non-empty")


@dataclass(frozen=True, slots=True)
class ModelTurn:
    """One assistant turn returned by the model adapter.

    `assistant_message` is deliberately retained in normalized model message
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
    """Provider-neutral input accepted by the model adapter."""

    messages: Sequence[Mapping[str, Any]]
    tools: Sequence[Mapping[str, Any]] = ()
    think: ThinkSetting | None = None
    options: Mapping[str, Any] | None = None
    response_format: Mapping[str, Any] | str | None = None
    on_timeout_diagnostic: Callable[[dict[str, Any]], None] | None = None
    on_stream_progress: Callable[[dict[str, Any]], None] | None = None
