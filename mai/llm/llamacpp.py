"""Strict llama.cpp OpenAI-compatible chat adapter. No automatic provider fallback."""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import httpx

from .models import ChatRequest, ModelConfig, ModelTurn, NativeToolCall
from .ollama import OllamaChatTimeoutError, OllamaProtocolError, OllamaRequestError


class LlamaCppAdapter:
    def __init__(self, config: ModelConfig, *, client: httpx.AsyncClient | None = None) -> None:
        self.config = config
        self._client = client

    async def chat(self, request: ChatRequest) -> ModelTurn:
        if request.options:
            raise ValueError("llama.cpp does not support MAI's Ollama-specific options")
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": [dict(message) for message in request.messages],
            "tools": [dict(tool) for tool in request.tools],
            "stream": False,
        }
        if request.response_format is not None:
            if isinstance(request.response_format, Mapping):
                payload["response_format"] = {"type": "json_schema", "json_schema": {
                    "name": "mai_response", "schema": dict(request.response_format), "strict": True,
                }}
            elif request.response_format == "json":
                payload["response_format"] = {"type": "json_object"}
            else:
                raise ValueError("Unsupported llama.cpp response format")
        think = self.config.think if request.think is None else request.think
        payload["reasoning_effort"] = "medium" if think is True else "none" if think is False else think
        async def send(client: httpx.AsyncClient) -> dict[str, Any]:
            response = await client.post(
                self.config.host.rstrip("/") + "/v1/chat/completions",
                json=payload,
                timeout=self.config.request_timeout_seconds,
            )
            response.raise_for_status()
            obj = response.json()
            if not isinstance(obj, dict):
                raise OllamaProtocolError("llama.cpp response must be an object")
            return obj
        try:
            if self._client is None:
                async with httpx.AsyncClient() as client:
                    result = await send(client)
            else:
                result = await send(self._client)
        except httpx.TimeoutException as exc:
            raise OllamaChatTimeoutError("llama.cpp chat request timed out") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise OllamaRequestError("llama.cpp chat request failed") from exc
        choices = result.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise OllamaProtocolError("llama.cpp response missing choices")
        msg = choices[0].get("message")
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            raise OllamaProtocolError("llama.cpp response missing assistant message")
        content = msg.get("content") or ""
        thinking = msg.get("reasoning_content") or ""
        if not isinstance(content, str) or not isinstance(thinking, str):
            raise OllamaProtocolError("llama.cpp content and reasoning must be strings")
        raw_calls = msg.get("tool_calls") or []
        if not isinstance(raw_calls, list):
            raise OllamaProtocolError("llama.cpp tool_calls must be an array")
        calls: list[NativeToolCall] = []
        for raw in raw_calls:
            if not isinstance(raw, dict) or not isinstance(raw.get("function"), dict):
                raise OllamaProtocolError("llama.cpp malformed tool call")
            function = raw["function"]
            name, args = function.get("name"), function.get("arguments")
            if not isinstance(name, str) or not name.strip():
                raise OllamaProtocolError("llama.cpp tool call missing function name")
            if isinstance(args, str):
                import json
                try:
                    args = json.loads(args)
                except json.JSONDecodeError as exc:
                    raise OllamaProtocolError("llama.cpp tool arguments are invalid JSON") from exc
            if not isinstance(args, dict):
                raise OllamaProtocolError("llama.cpp tool arguments must be an object")
            calls.append(NativeToolCall(name=name, arguments=args))
        assistant: dict[str, Any] = {"role": "assistant", "content": content}
        if thinking:
            assistant["thinking"] = thinking
        if calls:
            assistant["tool_calls"] = [
                {"type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                for c in calls
            ]
        return ModelTurn(content=content, thinking=thinking, tool_calls=tuple(calls), assistant_message=assistant)
