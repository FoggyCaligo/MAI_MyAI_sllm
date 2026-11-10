"""Optional cloud API adapters for MAI's provider-neutral agent loop.

Secrets are read only from environment/.env, never from settings.yaml.
No provider fallback. Tool calls are normalized to the existing MAI contract.
"""
from __future__ import annotations

import base64
import json
import os
from typing import Any

import httpx

from .models import ChatRequest, ModelConfig, ModelTurn, NativeToolCall
from .errors import ModelChatTimeoutError, ModelProtocolError, ModelRequestError
from .llamacpp import _openai_messages

PROVIDER_ENDPOINTS = {
    "openai": ("https://api.openai.com/v1/chat/completions", "OPENAI_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1/chat/completions", "OPENROUTER_API_KEY"),
    "anthropic": ("https://api.anthropic.com/v1/messages", "ANTHROPIC_API_KEY"),
}


def configured_models() -> tuple[str, ...]:
    """Explicit allowlist; an API key alone never enables paid API requests."""
    models: list[str] = []
    for provider, (_, key_name) in PROVIDER_ENDPOINTS.items():
        if not os.environ.get(key_name):
            continue
        value = os.environ.get(f"MAI_{provider.upper()}_MODELS", "")
        for model in value.split(","):
            if model.strip():
                models.append(f"{provider}:{model.strip()}")
    return tuple(models)


class CloudAPIAdapter:
    def __init__(self, provider: str, config: ModelConfig) -> None:
        if provider not in PROVIDER_ENDPOINTS:
            raise ValueError(f"Unsupported cloud provider: {provider}")
        self.provider = provider
        self.config = config

    async def chat(self, request: ChatRequest) -> ModelTurn:
        if request.options:
            raise ValueError("Cloud provider does not support custom MAI generation options")
        endpoint, key_name = PROVIDER_ENDPOINTS[self.provider]
        secret = os.environ.get(key_name, "").strip()
        if not secret:
            raise ModelRequestError(f"{key_name} is not configured in .env")
        if self.provider == "anthropic":
            payload = self._anthropic_payload(request)
            headers = {
                "x-api-key": secret, "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
        else:
            payload = self._openai_payload(request)
            headers = {"Authorization": f"Bearer {secret}"}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(self.config.request_timeout_seconds)) as client:
                response = await client.post(endpoint, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
        except httpx.TimeoutException as exc:
            raise ModelChatTimeoutError(f"{self.provider} chat request timed out") from exc
        except httpx.HTTPStatusError as exc:
            # Avoid logging response text, which can contain secrets or user content.
            raise ModelRequestError(f"{self.provider} API HTTP {exc.response.status_code}") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ModelRequestError(f"{self.provider} API request failed: {type(exc).__name__}") from exc
        if not isinstance(data, dict):
            raise ModelProtocolError(f"{self.provider} response is not an object")
        return self._anthropic_turn(data, request) if self.provider == "anthropic" else self._openai_turn(data, request)

    def _openai_payload(self, request: ChatRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": _openai_messages(request.messages),
            "stream": False,
        }
        if request.tools:
            payload["tools"] = [dict(tool) for tool in request.tools]
        if self.config.max_output_tokens is not None:
            payload["max_completion_tokens" if self.provider == "openai" else "max_tokens"] = self.config.max_output_tokens
        if request.response_format is not None:
            if request.response_format == "json":
                payload["response_format"] = {"type": "json_object"}
            elif isinstance(request.response_format, dict) or hasattr(request.response_format, "items"):
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "mai_response", "schema": dict(request.response_format), "strict": True},
                }
            else:
                raise ModelProtocolError("Unsupported response format")
        return payload

    def _anthropic_payload(self, request: ChatRequest) -> dict[str, Any]:
        normalized = _openai_messages(request.messages)
        system_parts: list[str] = []
        messages: list[dict[str, Any]] = []
        for message in normalized:
            role = message.get("role")
            content = message.get("content") or ""
            if role in ("system", "developer"):
                system_parts.append(str(content))
                continue
            if role == "tool":
                messages.append({"role": "user", "content": [{
                    "type": "tool_result", "tool_use_id": message["tool_call_id"],
                    "content": str(content),
                }]})
                continue
            blocks: list[dict[str, Any]] = []
            if isinstance(content, str) and content:
                blocks.append({"type": "text", "text": content})
            elif isinstance(content, list):
                for item in content:
                    if item.get("type") == "text":
                        blocks.append(item)
                    elif item.get("type") == "image_url":
                        raw = item["image_url"]["url"]
                        if not raw.startswith("data:") or ";base64," not in raw:
                            raise ModelProtocolError("Anthropic requires base64 inline images")
                        mime, encoded = raw[5:].split(";base64,", 1)
                        blocks.append({"type": "image", "source": {
                            "type": "base64", "media_type": mime, "data": encoded,
                        }})
            for call in message.get("tool_calls", []):
                blocks.append({"type": "tool_use", "id": call["id"],
                               "name": call["function"]["name"],
                               "input": json.loads(call["function"]["arguments"])})
            if not blocks:
                blocks.append({"type": "text", "text": " "})
            # Anthropic requires consecutive same-role messages to be merged.
            target_role = "assistant" if role == "assistant" else "user"
            if messages and messages[-1]["role"] == target_role:
                previous = messages[-1]["content"]
                if isinstance(previous, list):
                    previous.extend(blocks)
                else:
                    raise ModelProtocolError("Malformed Anthropic messages")
            else:
                messages.append({"role": target_role, "content": blocks})
        tools = []
        for item in request.tools:
            fn = item.get("function")
            if not isinstance(fn, dict):
                raise ModelProtocolError("Invalid MAI tool schema")
            tools.append({"name": fn["name"], "description": fn.get("description", ""),
                          "input_schema": fn["parameters"]})
        payload: dict[str, Any] = {
            "model": self.config.model,
            "system": "\n\n".join(system_parts),
            "messages": messages,
            "max_tokens": min(self.config.max_output_tokens or 8192, 16384),
        }
        if tools:
            payload["tools"] = tools
        if request.response_format is not None:
            # Anthropic schema support differs by model; constrain through system
            # instruction and validate the returned JSON in the existing verifier.
            if request.response_format == "json":
                schema_text = "a single JSON object"
            else:
                schema_text = json.dumps(request.response_format, ensure_ascii=False)
            payload["system"] += "\nReturn only JSON conforming to: " + schema_text
        return payload

    def _openai_turn(self, data: dict[str, Any], request: ChatRequest) -> ModelTurn:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ModelProtocolError("Cloud response missing choices")
        choice = choices[0]
        if choice.get("finish_reason") == "length":
            raise ModelProtocolError("Cloud output token limit reached")
        msg = choice.get("message")
        if not isinstance(msg, dict):
            raise ModelProtocolError("Cloud response missing message")
        content = msg.get("content") or ""
        if not isinstance(content, str):
            raise ModelProtocolError("Cloud response content must be text")
        calls: list[NativeToolCall] = []
        for raw in msg.get("tool_calls") or []:
            if raw.get("type") != "function":
                raise ModelProtocolError("Unsupported cloud tool call type")
            fn = raw.get("function") or {}
            arguments = fn.get("arguments", "{}")
            try:
                args = json.loads(arguments) if isinstance(arguments, str) else arguments
            except (ValueError, TypeError) as exc:
                raise ModelProtocolError("Cloud tool arguments are invalid JSON") from exc
            if not isinstance(args, dict):
                raise ModelProtocolError("Cloud tool arguments must be an object")
            calls.append(NativeToolCall(name=fn["name"], arguments=args))
        if request.response_format is not None and not content.strip():
            raise ModelProtocolError("Cloud structured response is empty")
        # Reasoning is never persisted as a fabricated response.
        normalized: dict[str, Any] = {"role": "assistant", "content": content}
        if calls:
            normalized["tool_calls"] = [
                {"type": "function", "function": {"name": c.name, "arguments": c.arguments}} for c in calls
            ]
        return ModelTurn(content=content, thinking="", tool_calls=tuple(calls), assistant_message=normalized)

    def _anthropic_turn(self, data: dict[str, Any], request: ChatRequest) -> ModelTurn:
        if data.get("stop_reason") == "max_tokens":
            raise ModelProtocolError("Anthropic output token limit reached")
        blocks = data.get("content")
        if not isinstance(blocks, list):
            raise ModelProtocolError("Anthropic response missing content")
        text_parts: list[str] = []
        calls: list[NativeToolCall] = []
        for block in blocks:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                args = block.get("input")
                if not isinstance(args, dict):
                    raise ModelProtocolError("Anthropic tool input must be an object")
                calls.append(NativeToolCall(name=block["name"], arguments=args))
        content = "\n".join(text_parts)
        if request.response_format is not None and not content.strip():
            raise ModelProtocolError("Anthropic structured response is empty")
        normalized: dict[str, Any] = {"role": "assistant", "content": content}
        if calls:
            normalized["tool_calls"] = [
                {"type": "function", "function": {"name": c.name, "arguments": c.arguments}} for c in calls
            ]
        return ModelTurn(content=content, thinking="", tool_calls=tuple(calls), assistant_message=normalized)
