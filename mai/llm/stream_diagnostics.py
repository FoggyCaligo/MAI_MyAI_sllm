"""Strict streaming assembler for diagnosing incomplete llama.cpp chat responses.

Only a fully terminated stream can produce a normal chat completion. Partial
text is available to the caller exclusively for failure-only audit recording.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

from .errors import ModelProtocolError


@dataclass
class PartialCompletion:
    thinking_parts: list[str] = field(default_factory=list)
    content_parts: list[str] = field(default_factory=list)
    tool_fragments: dict[int, dict[str, str]] = field(default_factory=dict)
    finish_reason: str | None = None
    saw_done: bool = False
    chunks: int = 0

    def snapshot(self) -> dict[str, Any]:
        return {
            "thinking": "".join(self.thinking_parts),
            "content": "".join(self.content_parts),
            "tool_fragments": [
                {"index": index, **parts}
                for index, parts in sorted(self.tool_fragments.items())
            ],
            "chunks": self.chunks,
            "finish_reason": self.finish_reason,
            "complete": self.saw_done,
        }

    def consume(self, data: str) -> None:
        if data == "[DONE]":
            if self.finish_reason is None:
                raise ModelProtocolError("llama.cpp stream ended without finish_reason")
            self.saw_done = True
            return
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ModelProtocolError("llama.cpp streaming chunk is not valid JSON") from exc
        if not isinstance(chunk, dict):
            raise ModelProtocolError("llama.cpp streaming chunk must be an object")
        choices = chunk.get("choices")
        if not isinstance(choices, list):
            raise ModelProtocolError("llama.cpp streaming choices must be a list")
        if not choices:
            return  # Usage-only trailer, not a model delta.
        if len(choices) != 1 or not isinstance(choices[0], dict):
            raise ModelProtocolError("llama.cpp streaming response must have one choice")
        choice = choices[0]
        if choice.get("index", 0) != 0:
            raise ModelProtocolError("llama.cpp streaming choice index must be 0")
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            raise ModelProtocolError("llama.cpp streaming choice missing delta")
        for source, target in (("reasoning_content", self.thinking_parts), ("content", self.content_parts)):
            part = delta.get(source)
            if part is not None:
                if not isinstance(part, str):
                    raise ModelProtocolError(f"llama.cpp streaming {source} must be text")
                target.append(part)
        calls = delta.get("tool_calls", [])
        if not isinstance(calls, list):
            raise ModelProtocolError("llama.cpp streaming tool_calls must be a list")
        for call in calls:
            if not isinstance(call, dict) or type(call.get("index")) is not int or call["index"] < 0:
                raise ModelProtocolError("llama.cpp streaming tool call missing index")
            parts = self.tool_fragments.setdefault(call["index"], {"id": "", "name": "", "arguments": ""})
            identifier = call.get("id")
            if identifier is not None:
                if not isinstance(identifier, str):
                    raise ModelProtocolError("llama.cpp streaming tool call id must be text")
                parts["id"] += identifier
            function = call.get("function")
            if function is not None:
                if not isinstance(function, dict):
                    raise ModelProtocolError("llama.cpp streaming tool call function must be an object")
                for key in ("name", "arguments"):
                    fragment = function.get(key)
                    if fragment is not None:
                        if not isinstance(fragment, str):
                            raise ModelProtocolError(f"llama.cpp streaming tool call {key} must be text")
                        parts[key] += fragment
        finish = choice.get("finish_reason")
        if finish is not None:
            if not isinstance(finish, str):
                raise ModelProtocolError("llama.cpp streaming finish_reason must be text")
            self.finish_reason = finish
        self.chunks += 1

    def completed_response(self) -> dict[str, Any]:
        if not self.saw_done or self.finish_reason is None:
            raise ModelProtocolError("llama.cpp streaming response did not complete")
        calls = [
            {"type": "function", "id": fragments["id"], "function": {
                "name": fragments["name"], "arguments": fragments["arguments"],
            }}
            for _, fragments in sorted(self.tool_fragments.items())
        ]
        return {
            "choices": [{
                "finish_reason": self.finish_reason,
                "message": {
                    "role": "assistant",
                    "reasoning_content": "".join(self.thinking_parts),
                    "content": "".join(self.content_parts),
                    "tool_calls": calls,
                },
            }],
        }


async def stream_completion(
    client: httpx.AsyncClient,
    url: str,
    payload: dict[str, Any],
    *,
    timeout_seconds: float,
    on_timeout: Callable[[dict[str, Any]], None] | None = None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    partial = PartialCompletion()
    stream_payload = {**payload, "stream": True}
    last_progress_at = 0.0
    try:
        # An absolute deadline is necessary: a model emitting tokens forever
        # must still time out even though each socket read succeeds.
        async with asyncio.timeout(timeout_seconds):
            async with client.stream(
                "POST", url, json=stream_payload, timeout=timeout_seconds,
            ) as response:
                response.raise_for_status()
                # Older llama.cpp adapters and controlled test doubles can
                # return an ordinary JSON completion despite stream=true.
                # Preserve compatibility, but use incremental SSE whenever
                # the provider supports it.
                if response.headers.get("content-type", "").lower().startswith("application/json"):
                    result = await response.aread()
                    try:
                        object_response = json.loads(result)
                    except (ValueError, TypeError) as exc:
                        raise ModelProtocolError("llama.cpp JSON fallback is invalid") from exc
                    if not isinstance(object_response, dict):
                        raise ModelProtocolError("llama.cpp JSON fallback must be an object")
                    return object_response
                pending_data: list[str] = []
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        pending_data.append(line[5:].lstrip(" "))
                    elif not line:
                        if pending_data:
                            partial.consume("\n".join(pending_data))
                            pending_data.clear()
                            now = time.monotonic()
                            if on_progress is not None and (now - last_progress_at >= 0.5 or partial.saw_done):
                                on_progress(partial.snapshot())
                                last_progress_at = now
                    elif line.startswith(":") or line.startswith("event:"):
                        continue
                    else:
                        raise ModelProtocolError("llama.cpp stream has an unsupported SSE field")
                    if partial.saw_done:
                        break
                if pending_data and not partial.saw_done:
                    partial.consume("\n".join(pending_data))
                    if on_progress is not None:
                        on_progress(partial.snapshot())
        return partial.completed_response()
    except (TimeoutError, httpx.TimeoutException):
        if on_timeout is not None:
            on_timeout(partial.snapshot())
        raise
