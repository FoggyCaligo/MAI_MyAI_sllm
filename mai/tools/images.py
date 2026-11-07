"""Image analysis via the active llama.cpp multimodal GGUF; no Ollama dependency.

The active model must support image content. A text-only model is not silently
replaced with another model, nor is visual content inferred from filenames.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..llm.models import ChatRequest
from .registry import ToolRegistry


MAX_IMAGE_BYTES = 20 * 1024 * 1024


class ImageAnalyzeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1)
    question: str = Field(
        default="Describe the image accurately and extract details relevant to the user's request.",
        min_length=1,
    )


def resolve_image_path(
    path: str, *, cwd: str | Path | None = None, allowed_root: str | Path | None = None
) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = Path(cwd or os.getcwd()) / candidate
    target = candidate.resolve(strict=False)
    if allowed_root is not None:
        root = Path(allowed_root).expanduser().resolve(strict=False)
        if not target.is_relative_to(root):
            raise PermissionError("image path is outside the permitted account directory")
    if not target.is_file():
        if not target.exists():
            raise FileNotFoundError(str(target))
        raise IsADirectoryError(str(target))
    if target.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError("image exceeds 20 MiB limit")
    return target


class ImageAnalyzer:
    def __init__(self, *, adapter: Any, cwd: str | Path | None = None,
                 allowed_root: str | Path | None = None):
        if not callable(getattr(adapter, "chat", None)):
            raise TypeError("image analyzer requires a llama.cpp chat adapter")
        self.adapter = adapter
        self.cwd = cwd
        self.allowed_root = allowed_root

    async def analyze(self, *, path: str, question: str) -> dict[str, Any]:
        target = resolve_image_path(path, cwd=self.cwd, allowed_root=self.allowed_root)
        # The llama.cpp adapter validates image signatures and sends image_url data.
        # The same active model serves text and images; no second GPU model starts.
        response = await self.adapter.chat(ChatRequest(
            messages=({"role": "user", "content": question, "images": [target.read_bytes()]},),
            tools=(),
            think=False,
        ))
        if response.tool_calls or not isinstance(response.content, str) or not response.content.strip():
            raise RuntimeError("image analyzer returned no usable visual analysis")
        return {
            "path": str(target),
            "model": self.adapter.config.model,
            "analysis": response.content,
        }


def register_image_tools(registry: ToolRegistry, *, adapter: Any,
                         cwd: str | Path | None = None,
                         allowed_root: str | Path | None = None,
                         timeout_seconds: float | None = 120) -> None:
    analyzer = ImageAnalyzer(adapter=adapter, cwd=cwd, allowed_root=allowed_root)
    registry.add(
        name="image_analyze",
        description=(
            "Analyze an image file with the currently loaded llama.cpp multimodal model. "
            "The configured GGUF must support image input and have its required "
            "multimodal projector loaded; text-only models fail explicitly. "
            "Read screenshots, photos, diagrams and charts from real accessible paths."
        ),
        input_model=ImageAnalyzeInput,
        handler=analyzer.analyze,
        timeout_seconds=timeout_seconds,
        category="image",
    )
