"""Structural catalog of local llama.cpp GGUF models.

Each direct child directory contains exactly one model GGUF and optionally one
mmproj GGUF. The folder is the association boundary, independent of model
or projector quantization names. Unsupported or ambiguous layouts fail visibly.
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
    """Discover exactly one model and at most one projector in each model folder."""
    root = root.resolve()
    for old_key in ("LLAMACPP_MAIN_MMPROJ_PATH", "LLAMACPP_VISION_MMPROJ_PATH", "LLAMACPP_MODEL_MMPROJ_MAP"):
        if config.get(old_key):
            raise ValueError(f"{old_key} is obsolete; place the model and mmproj in one folder")
    directory_raw = config.get("LLAMACPP_GGUF_DIRECTORY")
    main_raw = config.get("LLAMACPP_MAIN_GGUF_PATH")
    if not isinstance(directory_raw, str) or not directory_raw.strip():
        raise ValueError("LLAMACPP_GGUF_DIRECTORY is required")
    if not isinstance(main_raw, str) or not main_raw.strip():
        raise ValueError("LLAMACPP_MAIN_GGUF_PATH is required")
    directory = resolve_path(root, directory_raw)
    if not directory.is_dir():
        raise ValueError(f"GGUF directory does not exist: {directory}")
    default_path = resolve_path(root, main_raw)
    valid_gguf(default_path)
    if not default_path.is_relative_to(directory) or default_path.parent == directory:
        raise ValueError("Default GGUF must be inside a direct child model directory")
    if default_path.parent.parent != directory:
        raise ValueError("Nested GGUF model directories are unsupported")
    if is_mmproj_filename(default_path):
        raise ValueError("Default GGUF cannot be a projector")

    # A model folder is the authoritative association boundary. No model-name
    # matching, quantization matching, or explicit global projector fallback.
    entries: list[dict[str, str | None]] = []
    ids: set[str] = set()
    for folder in sorted(directory.iterdir()):
        if not folder.is_dir():
            continue
        if folder.is_symlink() or folder.resolve() != folder:
            raise ValueError(f"GGUF model folder must not be a symlink: {folder}")
        files = sorted(path for path in folder.iterdir() if path.is_file() and path.suffix.lower() == ".gguf")
        if not files:
            raise ValueError(f"GGUF model folder contains no GGUF file: {folder}")
        for path in files:
            if path.is_symlink() or path.resolve().parent != folder:
                raise ValueError(f"GGUF file must stay in its model folder: {path}")
            valid_gguf(path)
        models = [path for path in files if not is_mmproj_filename(path)]
        projectors = [path for path in files if is_mmproj_filename(path)]
        if len(models) != 1:
            raise ValueError(f"Expected exactly one model GGUF in {folder}, found {len(models)}")
        if len(projectors) > 1:
            raise ValueError(f"Expected at most one mmproj GGUF in {folder}, found {len(projectors)}")
        model = models[0].resolve(strict=True)
        projector = projectors[0].resolve(strict=True) if projectors else None
        model_id = "gguf-" + hashlib.sha256(str(model).encode("utf-8")).hexdigest()[:20]
        if model_id in ids:
            raise ValueError(f"Duplicate GGUF identity: {model}")
        ids.add(model_id)
        entries.append({
            "id": model_id, "label": model.name, "path": str(model),
            "projector": str(projector) if projector else None,
        })

    default_entry = next((entry for entry in entries if entry["path"] == str(default_path)), None)
    if default_entry is None:
        raise ValueError("Default GGUF was not found in a model directory")
    return {
        "default": default_entry["id"], "models": entries,
        "projector": default_entry["projector"],
        "main_vision": default_entry["projector"] is not None,
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
        if entry["projector"]:
            lines.append(f"mmproj = {entry['projector']}")
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
