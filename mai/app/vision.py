"""On-demand multimodal inspection with the dedicated llama.cpp router model.

The image never enters the text model KV cache. Only the vision model's
textual observations are supplied to the agent; access stays scoped to the
authenticated user's upload directory.
"""
from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Sequence

from ..app.uploads import principal_upload_directory
from ..llm.llamacpp import LlamaCppAdapter
from ..llm.models import ChatRequest, ModelConfig

_IMAGE_MIMES = frozenset({"image/png", "image/jpeg", "image/webp"})
_MAX_IMAGE_BYTES = 20 * 1024 * 1024


class VisionConfigurationError(RuntimeError):
    pass


async def inspect_uploaded_images(
    *, host: str, vision_model: str, prompt: str,
    attachments: Sequence[str], principal, upload_root: str | Path,
) -> str:
    """Return a bounded vision observation without exposing file paths to the model."""
    if not vision_model.strip():
        raise VisionConfigurationError("vision model is not configured")
    if not attachments:
        return ""
    folder = principal_upload_directory(upload_root, principal).resolve()
    parts: list[dict[str, object]] = [
        {"type": "text", "text": (
            "Describe the attached images accurately for another AI agent. "
            "Read visible text and identify objects or diagrams relevant to the "
            "user request. Clearly mark uncertain details. Do not claim to have "
            "performed actions or accessed any external resource. Request: " + prompt
        )}
    ]
    for name in attachments:
        if not name or Path(name).name != name or name in {".", ".."}:
            raise ValueError("invalid image attachment name")
        path = (folder / name).resolve()
        if path.parent != folder or not path.is_file():
            raise ValueError("image attachment is not in this account's upload directory")
        mime, _ = mimetypes.guess_type(path.name)
        if mime not in _IMAGE_MIMES:
            raise ValueError(f"vision input supports PNG/JPEG/WebP, not {mime or 'unknown'}")
        size = path.stat().st_size
        if size <= 0 or size > _MAX_IMAGE_BYTES:
            raise ValueError("vision input must be nonempty and at most 20 MiB per image")
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{encoded}"},
        })
    model = LlamaCppAdapter(ModelConfig(model=vision_model, host=host, think=False))
    answer = await model.chat(ChatRequest(
        messages=(
            {"role": "system", "content": "You are a careful visual observer; do not invent visual details."},
            {"role": "user", "content": parts},
        ),
        tools=(), think=False,
    ))
    if not answer.content.strip():
        raise VisionConfigurationError("vision model returned no observations")
    return answer.content.strip()
