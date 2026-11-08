"""Prepare validated uploaded images for the main model; llama.cpp never uses image tool fallback."""
from __future__ import annotations

from collections.abc import Sequence
import os
from pathlib import Path
from typing import Any

from .uploads import principal_upload_directory

MAX_IMAGE_BYTES = 20 * 1024 * 1024


async def prepare_main_image_input(
    *, prompt: str, attachments: Sequence[str], principal: Any,
    upload_root: str | Path, model: str,
) -> tuple[str, tuple[bytes, ...]]:
    """Use only configured llama.cpp Main vision; never invent image observations."""
    if not attachments:
        return prompt, ()
    directory = principal_upload_directory(upload_root, principal).resolve()
    paths: list[Path] = []
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
        paths.append(target)

    supports_vision = os.environ.get("LLAMACPP_MAIN_VISION", "false").lower() == "true"
    if supports_vision:
        return prompt, tuple(path.read_bytes() for path in paths)
    if not supports_vision:
        # Images cannot be inferred by a text-only model. Preserve the user
        # request, but never provide file paths that suggest an image tool
        # fallback or silently claim that the model has seen pixels.
        return (
            prompt + "\n\n[MAI runtime attachment notice: The user attached image file(s), "
            "but this main model has no enabled image input capability. "
            "No image pixels were provided to the model and image_analyze is "
            "not available. Tell the user you cannot inspect these images "
            "with the current model. Do not guess or describe their contents.]",
            (),
        )
