"""Prepare validated user-uploaded images for a vision-capable main Ollama model.

This path is optional; the separately configured image_analyze tool remains available.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .uploads import principal_upload_directory

MAX_IMAGE_BYTES = 20 * 1024 * 1024


def _capabilities(response: Any) -> frozenset[str]:
    value = response.get("capabilities") if isinstance(response, Mapping) else getattr(response, "capabilities", None)
    if not isinstance(value, (list, tuple, set, frozenset)):
        raise RuntimeError("Ollama model show response did not provide capabilities")
    return frozenset(str(item).lower() for item in value)


async def prepare_main_image_input(
    *, prompt: str, attachments: Sequence[str], principal: Any,
    upload_root: str | Path, model: str, backend: str, ollama_client: Any,
    separate_vision_model: str | None,
) -> tuple[str, tuple[bytes, ...]]:
    """Return augmented text and inline images, never trusting client-supplied paths.

    A vision-capable Ollama main model receives the original image bytes.
    Otherwise preserve the existing separate image tool route with explicit
    sandboxed file paths. Missing vision capability never silently discards
    user attachments.
    """
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

    supports_vision = False
    if backend == "ollama":
        show = await ollama_client.show(model)
        supports_vision = "vision" in _capabilities(show)
    if supports_vision:
        return prompt, tuple(path.read_bytes() for path in paths)
    if separate_vision_model is None:
        raise RuntimeError(
            "Selected main model does not expose Ollama vision capability and "
            "no separate VISION_MODEL image analyzer is configured"
        )
    details = "\n".join(f"- {path}" for path in paths)
    return (
        prompt + "\n\nAttached image files (not yet visually interpreted):\n"
        + details + "\nActivate image_analyze and inspect these files before making claims about their contents.",
        (),
    )
