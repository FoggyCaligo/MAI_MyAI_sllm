"""Validate user-uploaded image attachments before llama.cpp multimodal input."""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .uploads import principal_upload_directory
from ..tools.images import MAX_IMAGE_BYTES


def prepare_main_image_input(
    *, prompt: str, attachments: Sequence[str], principal: Any,
    upload_root: str | Path,
) -> tuple[str, tuple[bytes, ...]]:
    if not attachments:
        return prompt, ()
    directory = principal_upload_directory(upload_root, principal).resolve()
    images: list[bytes] = []
    for name in attachments:
        if not isinstance(name, str) or not name or name in {".", ".."}:
            raise ValueError("invalid image attachment name")
        if Path(name).name != name or "/" in name or "\\" in name:
            raise ValueError("image attachment must be a filename")
        target = (directory / name).resolve()
        if target.parent != directory or not target.is_file():
            raise FileNotFoundError("attached image not found in account upload directory")
        if target.stat().st_size > MAX_IMAGE_BYTES:
            raise ValueError("attached image exceeds 20 MiB limit")
        images.append(target.read_bytes())
    return prompt, tuple(images)
