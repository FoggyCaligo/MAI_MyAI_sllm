"""Structural catalog of local llama.cpp GGUF models.

The only discovery criterion is a regular GGUF file in the configured model
directory, excluding explicitly configured multimodal projector paths and
files conventionally named mmproj-*.gguf or mmproj_*.gguf.
No model capability is inferred from a filename; the mmproj rule excludes a
known auxiliary-file naming convention.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from portable.resolve_llamacpp_settings import read_config


def resolve_path(root: Path, raw: str) -> Path:
    path = Path(raw).expanduser()
    return (path if path.is_absolute() else root / path).resolve(strict=True)


def valid_gguf(path: Path) -> None:
    if not path.is_file() or path.suffix.lower() != ".gguf":
        raise ValueError(f"Not a GGUF file: {path}")
    with path.open("rb") as stream:
        if stream.read(4) != b"GGUF":
            raise ValueError(f"Invalid GGUF header: {path}")
    if "\n" in str(path) or "\r" in str(path):
        raise ValueError(f"Newline in GGUF path: {path}")


def is_mmproj_filename(path: Path) -> bool:
    """Exclude unconfigured projector files before registering model aliases."""
    return path.name.lower().startswith(("mmproj-", "mmproj_"))


def discover(root: Path, config: dict[str, object]) -> dict[str, object]:
    root = root.resolve()
    main_raw = config.get("LLAMACPP_MAIN_GGUF_PATH")
    if not isinstance(main_raw, str) or not main_raw.strip():
        raise ValueError("LLAMACPP_MAIN_GGUF_PATH is required as the default model")
    default_path = resolve_path(root, main_raw)
    valid_gguf(default_path)
    if is_mmproj_filename(default_path):
        raise ValueError("Default main GGUF cannot be an mmproj projector")
    directory_raw = config.get("LLAMACPP_GGUF_DIRECTORY")
    directory = (resolve_path(root, directory_raw) if isinstance(directory_raw, str)
                 and directory_raw.strip() else default_path.parent)
    if not directory.is_dir():
        raise ValueError(f"GGUF directory does not exist: {directory}")
    if not default_path.is_relative_to(directory):
        raise ValueError("Default main GGUF must be in the configured GGUF directory")
    excluded: set[Path] = set()
    for key in ("LLAMACPP_MAIN_MMPROJ_PATH", "LLAMACPP_VISION_MMPROJ_PATH"):
        raw = config.get(key)
        if isinstance(raw, str) and raw.strip():
            path = resolve_path(root, raw)
            valid_gguf(path)
            excluded.add(path)
    if default_path in excluded:
        raise ValueError("Main model cannot also be a projector")
    entries: list[dict[str, str]] = []
    ids: set[str] = set()
    for path in sorted(directory.glob("*.gguf")):
        resolved = path.resolve(strict=True)
        if resolved in excluded or is_mmproj_filename(resolved):
            continue
        valid_gguf(resolved)
        # Stable ID comes from the exact canonical path, not a naming heuristic.
        model_id = "gguf-" + hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:20]
        if model_id in ids:
            raise ValueError(f"Duplicate GGUF identity: {resolved}")
        ids.add(model_id)
        entries.append({"id": model_id, "label": resolved.name, "path": str(resolved)})
    default = next((entry["id"] for entry in entries if Path(entry["path"]) == default_path), None)
    if default is None:
        raise ValueError("Default main GGUF was not discovered")
    projector_raw = config.get("LLAMACPP_MAIN_MMPROJ_PATH")
    projector = resolve_path(root, projector_raw) if isinstance(projector_raw, str) and projector_raw else None
    return {
        "default": default, "models": entries,
        "projector": str(projector) if projector else None,
        "main_vision": bool(config.get("LLAMACPP_MAIN_VISION")),
        "reasoning_preserve": config.get("LLAMACPP_REASONING_PRESERVE", False),
        "cache_ram_mib": int(config.get("LLAMACPP_CACHE_RAM_MIB", 8192)),
        "memory_guard_enabled": config.get("LLAMACPP_MEMORY_GUARD_ENABLED", True),
        "min_free_vram_mib": int(config.get("LLAMACPP_MIN_FREE_VRAM_MIB", 1024)),
        "min_available_ram_mib": int(config.get("MAI_MIN_AVAILABLE_RAM_MIB", 6144)),
        "context_size": int(config.get("LLAMACPP_CONTEXT_SIZE", 131072)),
    }


def from_settings(root: Path) -> dict[str, object]:
    # No process/environment values are used to decide which local files exist.
    return discover(root, read_config(root / "settings.yaml"))


def render_ini(catalog: dict[str, object], *, context_size: int, gpu_layers: int) -> str:
    lines = ["version = 1", "[*]", f"ctx-size = {context_size}",
             f"n-gpu-layers = {gpu_layers}", "parallel = 1", "fit = off"]
    for entry in catalog["models"]:
        lines.extend([f"[{entry['id']}]", f"model = {entry['path']}"])
        if entry["id"] == catalog["default"] and catalog["projector"]:
            lines.append(f"mmproj = {catalog['projector']}")
    return "\n".join(lines) + "\n"


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--ini", type=Path, required=True)
    parser.add_argument("--context-size", type=int, default=None)
    parser.add_argument("--gpu-layers", type=int, default=99)
    args = parser.parse_args()
    if args.gpu_layers < 0:
        raise ValueError("Invalid llama.cpp GPU layer settings")
    catalog = from_settings(args.root)
    context_size = args.context_size if args.context_size is not None else catalog["context_size"]
    if not 512 <= context_size <= 262144:
        raise ValueError("Invalid llama.cpp context setting")
    args.ini.write_text(render_ini(catalog, context_size=context_size,
                                  gpu_layers=args.gpu_layers), encoding="utf-8")
    print(json.dumps(catalog, ensure_ascii=False))


if __name__ == "__main__":
    main()
