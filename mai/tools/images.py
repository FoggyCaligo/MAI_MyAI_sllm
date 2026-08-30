"""Image-reading tools backed by a separately configurable Ollama vision model."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from ollama import AsyncClient
from pydantic import BaseModel, ConfigDict, Field

from .registry import ToolRegistry


class ImageAnalyzeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1)
    question: str = Field(
        default="Describe the image accurately and extract details relevant to the user's request.",
        min_length=1,
    )


def _resolve(path: str, cwd: str | Path | None) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = Path(cwd or os.getcwd()) / candidate
    return candidate.resolve(strict=False)


class ImageAnalyzer:
    def __init__(
        self,
        *,
        model: str,
        host: str,
        cwd: str | Path | None = None,
        max_bytes: int = 20 * 1024 * 1024,
    ) -> None:
        clean_model = model.strip()
        if not clean_model:
            raise ValueError("vision model must be non-empty")
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.model = clean_model
        self.cwd = cwd
        self.max_bytes = max_bytes
        self.client = AsyncClient(host=host)

    async def analyze(self, *, path: str, question: str) -> dict[str, Any]:
        target = _resolve(path, self.cwd)
        if not target.exists():
            raise FileNotFoundError(str(target))
        if not target.is_file():
            raise IsADirectoryError(str(target))
        size = target.stat().st_size
        if size > self.max_bytes:
            raise ValueError(f"image exceeds configured size limit: {size} > {self.max_bytes}")

        response = await self.client.chat(
            model=self.model,
            messages=[{
                "role": "user",
                "content": question,
                "images": [target.read_bytes()],
            }],
        )
        message = getattr(response, "message", None)
        if message is None and isinstance(response, Mapping):
            message = response.get("message")
        content = getattr(message, "content", None)
        if content is None and isinstance(message, Mapping):
            content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("vision model response is missing message content")
        return {
            "path": str(target),
            "model": self.model,
            "analysis": content,
        }


def register_image_tools(
    registry: ToolRegistry,
    *,
    model: str,
    host: str,
    cwd: str | Path | None = None,
    timeout_seconds: float | None = 120,
) -> None:
    analyzer = ImageAnalyzer(model=model, host=host, cwd=cwd)
    registry.add(
        name="image_analyze",
        description=(
            "Analyze a local image with the configured Ollama vision model. Use this for screenshots, "
            "photos, charts, diagrams, or other image files whose visual content must be understood. "
            "The path must be a real local path established by the conversation or a tool result; do not invent "
            "placeholder paths. If the path is unknown, ambiguous, or previously produced FileNotFoundError, first "
            "discover the actual file with an available filesystem search/list tool, then call image_analyze with "
            "the discovered path. A failed call does not make a later call with corrected evidence invalid."
        ),
        input_model=ImageAnalyzeInput,
        handler=analyzer.analyze,
        timeout_seconds=timeout_seconds,
        category="image",
    )
