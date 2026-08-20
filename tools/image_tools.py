from __future__ import annotations

from pathlib import Path
from typing import Any
import fnmatch

from .. import config
from .ollama_client import image_chat
from .tool_runtime import ToolDefinition, ToolRegistry


SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}


class ImageAnalyzeToolSuite:
    def __init__(self, workspace_root: Path | None = None) -> None:
        self._workspace_root = (workspace_root or config.WORKSPACE_ROOT).resolve()

    def build_registry(self) -> ToolRegistry:
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="image_analyze",
                description=(
                    "Inspect an image file. Always returns basic metadata. If an Ollama "
                    "vision-capable model is configured or provided, also returns a text "
                    "description of visible content."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "prompt": {"type": "string"},
                        "model": {"type": ["string", "null"]},
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            ),
            self._analyze,
        )
        return registry

    def _resolve(self, relative_path: str) -> Path:
        raw_path = Path(relative_path)
        target = raw_path.resolve() if raw_path.is_absolute() else (self._workspace_root / raw_path).resolve()
        if target.exists():
            return target
        if "?" not in raw_path.name:
            return target
        parent = target.parent
        if not parent.exists() or not parent.is_dir():
            return target
        matches = [
            item
            for item in parent.iterdir()
            if item.is_file() and fnmatch.fnmatchcase(item.name, raw_path.name)
        ]
        return matches[0].resolve() if len(matches) == 1 else target

    async def _analyze(self, arguments: dict[str, Any]) -> dict[str, Any]:
        relative_path = str(arguments.get("path") or "").strip()
        if not relative_path:
            raise ValueError("image_analyze requires path")

        target = self._resolve(relative_path)
        if not target.exists():
            return {
                "ok": False,
                "path": relative_path,
                "error": "not_found",
                "message": f"File not found: {relative_path}",
            }
        if not target.is_file():
            return {
                "ok": False,
                "path": relative_path,
                "error": "not_file",
                "message": f"Path is not a file: {relative_path}",
            }
        if target.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
            return {
                "ok": False,
                "path": relative_path,
                "error": "unsupported_image_type",
                "message": "image_analyze supports PNG, JPEG, WEBP, BMP, and GIF files.",
            }

        try:
            from PIL import Image, UnidentifiedImageError
        except ModuleNotFoundError:
            return {
                "ok": False,
                "path": relative_path,
                "error": "missing_dependency",
                "message": "image_analyze requires the pillow package. Install MK5 requirements or run: pip install pillow",
            }

        try:
            with Image.open(target) as image:
                metadata = {
                    "format": image.format,
                    "width": image.width,
                    "height": image.height,
                    "mode": image.mode,
                    "frames": getattr(image, "n_frames", 1),
                }
        except UnidentifiedImageError:
            return {
                "ok": False,
                "path": relative_path,
                "error": "invalid_image",
                "message": "Could not identify image file.",
            }

        requested_model = arguments.get("model")
        model = str(requested_model).strip() if isinstance(requested_model, str) and requested_model.strip() else None
        configured_model = model or config.OLLAMA_IMAGE_MODEL_NAME or config.OLLAMA_MODEL_NAME
        if not configured_model:
            return {
                "ok": True,
                "path": relative_path,
                "image": metadata,
                "vision_model_used": None,
                "description": None,
                "message": (
                    "Image metadata was read, but no Ollama model is configured. Set "
                    "MK5_OLLAMA_IMAGE_MODEL_NAME, OLLAMA_MODEL_NAME, or pass model to image_analyze "
                    "for visual recognition."
                ),
            }

        prompt = str(arguments.get("prompt") or "").strip() or (
            "Describe the visible contents of this image. If there is text, transcribe it. "
            "Answer concisely in Korean."
        )
        image_bytes = target.read_bytes()
        try:
            description = await image_chat(
                image_bytes=image_bytes,
                prompt=prompt,
                model=configured_model,
            )
        except Exception as exc:
            fallback_model = config.OLLAMA_IMAGE_FALLBACK_MODEL_NAME
            if fallback_model and fallback_model != configured_model:
                try:
                    fallback_description = await image_chat(
                        image_bytes=image_bytes,
                        prompt=prompt,
                        model=fallback_model,
                    )
                    return {
                        "ok": True,
                        "path": relative_path,
                        "image": metadata,
                        "vision_model_used": fallback_model,
                        "description": fallback_description,
                        "warning": (
                            f"Primary vision model '{configured_model}' failed "
                            f"({type(exc).__name__}: {exc}); used fallback vision model '{fallback_model}'."
                        ),
                    }
                except Exception:
                    pass
            return {
                "ok": False,
                "path": relative_path,
                "image": metadata,
                "vision_model_used": configured_model,
                "error": "vision_model_failed",
                "message": f"Image metadata was read, but vision analysis failed: {type(exc).__name__}: {exc}",
            }

        return {
            "ok": True,
            "path": relative_path,
            "image": metadata,
            "vision_model_used": configured_model,
            "description": description,
        }
