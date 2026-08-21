from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from .config import settings


class ModelContractError(RuntimeError):
    pass


@dataclass(slots=True)
class OllamaModel:
    timeout_seconds: float = 180.0
    num_predict: int = 2048

    async def structured(
        self,
        *,
        system: str,
        user: dict[str, Any],
        schema: dict[str, Any],
        model: str | None = None,
    ) -> dict[str, Any]:
        model_name = (model or settings.ollama_model).strip()
        if not model_name:
            raise ValueError("No Ollama model configured")
        payload = {
            "model": model_name,
            "stream": False,
            "think": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
            ],
            "format": schema,
            "options": {"num_predict": self.num_predict},
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(f"{settings.ollama_host}/api/chat", json=payload)
        response.raise_for_status()
        body = response.json()
        message = body.get("message")
        raw = message.get("content") if isinstance(message, dict) else None
        if not isinstance(raw, str) or not raw.strip():
            raise ModelContractError("Ollama returned an empty structured response")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ModelContractError(f"Ollama returned invalid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ModelContractError("Ollama structured response must be an object")
        return data

    async def list_models(self) -> list[str]:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{settings.ollama_host}/api/tags")
        response.raise_for_status()
        body = response.json()
        return [
            str(item["name"])
            for item in body.get("models", [])
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        ]
