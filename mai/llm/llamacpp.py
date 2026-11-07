"""Strict llama.cpp OpenAI-compatible chat adapter. No automatic provider fallback."""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import httpx

from .models import ChatRequest, ModelConfig, ModelTurn, NativeToolCall
from .errors import ModelTimeoutError, ModelProtocolError, ModelRequestError


class LlamaCppAdapter:
    def __init__(self, config: ModelConfig, *, client: httpx.AsyncClient | None = None,
                 slot_id: int | None = None) -> None:
        if slot_id is not None and slot_id < 0:
            raise ValueError("slot_id must be nonnegative")
        self.config = config
        self._client = client
        self.slot_id = slot_id

    async def chat(self, request: ChatRequest) -> ModelTurn:
        if request.options:
            raise ValueError("llama.cpp does not support MAI's Ollama-specific options")
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": _openai_messages(request.messages),
            "tools": [dict(tool) for tool in request.tools],
            "stream": False,
        }
        if self.slot_id is not None:
            # Explicit slot affinity is required for checkpoint correctness.
            # A dedicated --parallel 1 llama.cpp server is required by the caller.
            payload["id_slot"] = self.slot_id
            payload["cache_prompt"] = True
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
                raise ModelProtocolError("llama.cpp response must be an object")
            return obj
        try:
            if self._client is None:
                async with httpx.AsyncClient() as client:
                    result = await send(client)
            else:
                result = await send(self._client)
        except httpx.TimeoutException as exc:
            raise ModelTimeoutError("llama.cpp chat request timed out") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ModelRequestError("llama.cpp chat request failed") from exc
        choices = result.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ModelProtocolError("llama.cpp response missing choices")
        msg = choices[0].get("message")
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            raise ModelProtocolError("llama.cpp response missing assistant message")
        content = msg.get("content") or ""
        thinking = msg.get("reasoning_content") or ""
        if not isinstance(content, str) or not isinstance(thinking, str):
            raise ModelProtocolError("llama.cpp content and reasoning must be strings")
        raw_calls = msg.get("tool_calls") or []
        if not isinstance(raw_calls, list):
            raise ModelProtocolError("llama.cpp tool_calls must be an array")
        calls: list[NativeToolCall] = []
        for raw in raw_calls:
            if not isinstance(raw, dict) or not isinstance(raw.get("function"), dict):
                raise ModelProtocolError("llama.cpp malformed tool call")
            function = raw["function"]
            name, args = function.get("name"), function.get("arguments")
            if not isinstance(name, str) or not name.strip():
                raise ModelProtocolError("llama.cpp tool call missing function name")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError as exc:
                    raise ModelProtocolError("llama.cpp tool arguments are invalid JSON") from exc
            if not isinstance(args, dict):
                raise ModelProtocolError("llama.cpp tool arguments must be an object")
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


def _openai_message(message: Mapping[str, Any]) -> dict[str, Any]:
    """Convert MAI tool-call argument objects to OpenAI JSON strings."""
    result = dict(message)
    calls = result.get("tool_calls")
    if calls is None:
        return result
    if not isinstance(calls, list):
        raise ModelProtocolError("outgoing tool_calls must be a list")
    normalized = []
    for call in calls:
        if not isinstance(call, dict) or not isinstance(call.get("function"), dict):
            raise ModelProtocolError("outgoing tool call is malformed")
        function = dict(call["function"])
        arguments = function.get("arguments")
        if not isinstance(arguments, dict):
            raise ModelProtocolError("outgoing tool-call arguments must be an object")
        function["arguments"] = json.dumps(arguments, ensure_ascii=False)
        normalized.append({"type": "function", "function": function})
    result["tool_calls"] = normalized
    return result

 
def _openai_messages(messages: Any) -> list[dict[str, Any]]:
    """Pair Ollama-style tool result messages with stable OpenAI tool_call_id values."""
    out: list[dict[str, Any]] = []
    pending: list[tuple[str, str]] = []
    for turn_index, original in enumerate(messages):
        message = dict(original)
        if message.get("role") == "tool":
            tool_name = message.pop("tool_name", None)
            if not pending:
                raise ModelProtocolError("tool result without preceding assistant tool call")
            expected_name, call_id = pending.pop(0)
            if tool_name != expected_name:
                raise ModelProtocolError("tool result name does not match preceding tool call")
            message["tool_call_id"] = call_id
            out.append(message)
            continue
        normalized = _openai_message(message)
        normalized.pop("thinking", None)
        if normalized.get("tool_calls"):
            for index, tool_call in enumerate(normalized["tool_calls"]):
                call_id = f"call_{turn_index}_{index}"
                tool_call["id"] = call_id
                pending.append((tool_call["function"]["name"], call_id))
        out.append(normalized)
    if pending:
        raise ModelProtocolError("assistant tool calls are missing tool results")
    return out
