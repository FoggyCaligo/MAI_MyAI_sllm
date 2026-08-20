"""Ollama helpers for MK5 chat-model selection and execution."""
from __future__ import annotations

from typing import Any
import base64

import httpx

from .. import config


_EMBEDDING_ONLY_FAMILIES: frozenset[str] = frozenset({"nomic-bert", "bert", "clip"})


class OllamaOutputTruncatedError(RuntimeError):
    pass


def _payload(
    *,
    model: str,
    system: str,
    user: str,
    response_format: str | dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "stream": False,
        "think": config.OLLAMA_THINK,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "options": {"num_predict": config.OLLAMA_NUM_PREDICT},
    }
    if response_format is not None:
        payload["format"] = response_format
    return payload


async def chat(
    *,
    system: str,
    user: str,
    model: str | None = None,
    response_format: str | dict[str, Any] | None = "json",
) -> str:
    model_name = model or config.OLLAMA_MODEL_NAME
    if not model_name:
        raise ValueError("No Ollama model configured.")

    async with httpx.AsyncClient(timeout=config.OLLAMA_TIMEOUT_SECONDS) as client:
        response = await client.post(
            f"{config.OLLAMA_HOST}/api/chat",
            json=_payload(
                model=model_name,
                system=system,
                user=user,
                response_format=response_format,
            ),
        )

    if response.status_code == 400:
        raise ValueError(f"Ollama rejected chat request for model '{model_name}'.")
    response.raise_for_status()
    data = response.json()
    message = data.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError(f"Ollama returned an empty response for model '{model_name}'.")
    if data.get("done_reason") == "length":
        raise OllamaOutputTruncatedError(
            f"Ollama output token limit reached for model '{model_name}' "
            f"(num_predict={config.OLLAMA_NUM_PREDICT}, output_chars={len(content)})."
        )
    return content


async def image_chat(
    *,
    image_bytes: bytes,
    prompt: str,
    model: str | None = None,
) -> str:
    model_name = model or config.OLLAMA_IMAGE_MODEL_NAME
    if not model_name:
        raise ValueError("No Ollama image model configured.")

    payload: dict[str, Any] = {
        "model": model_name,
        "stream": False,
        "think": config.OLLAMA_THINK,
        "messages": [
            {
                "role": "user",
                "content": prompt,
                "images": [base64.b64encode(image_bytes).decode("ascii")],
            }
        ],
        "options": {"num_predict": config.OLLAMA_NUM_PREDICT},
    }
    async with httpx.AsyncClient(timeout=config.OLLAMA_TIMEOUT_SECONDS) as client:
        response = await client.post(f"{config.OLLAMA_HOST}/api/chat", json=payload)

    if response.status_code == 400:
        raise ValueError(f"Ollama rejected image request for model '{model_name}'.")
    response.raise_for_status()
    data = response.json()
    message = data.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError(f"Ollama returned an empty image response for model '{model_name}'.")
    return content


async def list_models() -> list[str]:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{config.OLLAMA_HOST}/api/tags")
            response.raise_for_status()
            payload = response.json()
    except Exception:
        return []

    models: list[str] = []
    for item in payload.get("models", []):
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        if name in config.OLLAMA_EXCLUDED_MODELS:
            continue
        details = item.get("details") or {}
        families: list[str] = details.get("families") or []
        if not families:
            family = details.get("family")
            if isinstance(family, str) and family:
                families = [family]
        if families and all(family in _EMBEDDING_ONLY_FAMILIES for family in families):
            continue
        models.append(name)
    return models

