"""Thin Ollama adapter for messages, thinking, and native tool calls.

This layer does not execute tools and does not own the agent loop. Its only
responsibility is translating between MAI's provider-neutral contracts and
Ollama's native chat protocol.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from ollama import AsyncClient, ResponseError

from .models import ChatRequest, ModelConfig, ModelTurn, NativeToolCall


class OllamaAdapterError(RuntimeError):
    """Base class for adapter-visible Ollama failures."""


class OllamaRequestError(OllamaAdapterError):
    """Ollama rejected or failed a chat request."""


class OllamaProtocolError(OllamaAdapterError):
    """Ollama returned a response that violates the native chat contract."""


class OllamaChatClient(Protocol):
    async def chat(self, **kwargs: Any) -> Any: ...


class OllamaAdapter:
    """Execute one native Ollama chat turn without hiding model/tool semantics."""

    def __init__(self, config: ModelConfig, *, client: OllamaChatClient | None = None) -> None:
        self.config = config
        self._client: OllamaChatClient = client or AsyncClient(host=config.host)

    async def chat(self, request: ChatRequest) -> ModelTurn:
        think = self.config.think if request.think is None else request.think
        options = dict(self.config.options)
        if request.options is not None:
            options.update(request.options)

        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": [dict(message) for message in request.messages],
            "tools": [dict(tool) for tool in request.tools],
            "think": think,
            "stream": False,
        }
        if options:
            payload["options"] = options

        try:
            response = await self._client.chat(**payload)
        except ResponseError as exc:
            raise OllamaRequestError("Ollama chat request failed") from exc

        return _normalize_response(response)


def _normalize_response(response: Any) -> ModelTurn:
    message = _read_field(response, "message")
    if message is None:
        raise OllamaProtocolError("Ollama response is missing message")

    role = _read_field(message, "role") or "assistant"
    if role != "assistant":
        raise OllamaProtocolError("Ollama response message role must be assistant")

    content = _read_field(message, "content")
    thinking = _read_field(message, "thinking")
    raw_tool_calls = _read_field(message, "tool_calls") or []

    if content is None:
        content = ""
    if thinking is None:
        thinking = ""
    if not isinstance(content, str):
        raise OllamaProtocolError("Ollama message.content must be a string")
    if not isinstance(thinking, str):
        raise OllamaProtocolError("Ollama message.thinking must be a string")
    if not isinstance(raw_tool_calls, (list, tuple)):
        raise OllamaProtocolError("Ollama message.tool_calls must be a sequence")

    calls = tuple(_normalize_tool_call(call) for call in raw_tool_calls)
    assistant_message: dict[str, Any] = {
        "role": "assistant",
        "content": content,
    }
    if thinking:
        assistant_message["thinking"] = thinking
    if calls:
        assistant_message["tool_calls"] = [
            {
                "type": "function",
                "function": {
                    **({"index": call.index} if call.index is not None else {}),
                    "name": call.name,
                    "arguments": dict(call.arguments),
                },
            }
            for call in calls
        ]

    return ModelTurn(
        content=content,
        thinking=thinking,
        tool_calls=calls,
        assistant_message=assistant_message,
    )


def _normalize_tool_call(raw_call: Any) -> NativeToolCall:
    function = _read_field(raw_call, "function")
    if function is None:
        raise OllamaProtocolError("Ollama tool call is missing function")

    name = _read_field(function, "name")
    arguments = _read_field(function, "arguments")
    index = _read_field(function, "index")

    if not isinstance(name, str) or not name.strip():
        raise OllamaProtocolError("Ollama tool call function.name must be non-empty")
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, Mapping):
        raise OllamaProtocolError("Ollama tool call function.arguments must be an object")
    if index is not None and not isinstance(index, int):
        raise OllamaProtocolError("Ollama tool call function.index must be an integer when present")

    return NativeToolCall(name=name, arguments=dict(arguments), index=index)


def _read_field(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)
