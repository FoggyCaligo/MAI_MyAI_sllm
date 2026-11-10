"""Read validated llama.cpp-only GGUF catalog settings from settings.yaml.

Read the same strictly validated YAML as the MAI process, but do not modify
the parent process environment. JSON is consumed by the PowerShell launcher.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mai.app.settings import load_settings

KEYS = (
    "LLAMACPP_GGUF_DIRECTORY",
    "LLAMACPP_MAIN_GGUF_PATH",
    "LLAMACPP_MAIN_MMPROJ_PATH",
    "LLAMACPP_MAIN_VISION",
    "LLAMACPP_REASONING_PRESERVE",
    "LLAMACPP_CACHE_RAM_MIB",
    "LLAMACPP_MEMORY_GUARD_ENABLED",
    "LLAMACPP_MIN_FREE_VRAM_MIB",
    "MAI_MIN_AVAILABLE_RAM_MIB",
    "LLAMACPP_CONTEXT_SIZE",
    "LLAMACPP_VISION_GGUF_PATH",
    "LLAMACPP_VISION_MMPROJ_PATH",
)

def read_config(path: Path) -> dict[str, str | bool | None]:
    # YAML is authoritative for the configured GGUF paths.
    data = load_settings(path, environ={})
    missing = [key for key in ("LLAMACPP_MAIN_GGUF_PATH",) if key not in data]
    if missing:
        raise ValueError("Missing llama.cpp GGUF setting(s): " + ", ".join(missing))
    raw_vision = data.get("LLAMACPP_MAIN_VISION", "false").lower()
    if raw_vision not in {"true", "false"}:
        raise ValueError("LLAMACPP_MAIN_VISION must be true or false")
    result: dict[str, str | bool | None] = {key: data.get(key) for key in KEYS}
    result["LLAMACPP_MAIN_VISION"] = raw_vision == "true"
    raw_preserve = data.get("LLAMACPP_REASONING_PRESERVE", "false").lower()
    if raw_preserve not in {"true", "false"}:
        raise ValueError("LLAMACPP_REASONING_PRESERVE must be true or false")
    result["LLAMACPP_REASONING_PRESERVE"] = raw_preserve == "true"
    raw_guard = data.get("LLAMACPP_MEMORY_GUARD_ENABLED", "true").lower()
    if raw_guard not in {"true", "false"}:
        raise ValueError("LLAMACPP_MEMORY_GUARD_ENABLED must be true or false")
    result["LLAMACPP_MEMORY_GUARD_ENABLED"] = raw_guard == "true"
    for key, default, minimum, maximum in (
        ("LLAMACPP_MIN_FREE_VRAM_MIB", 1024, 128, 65536),
        ("MAI_MIN_AVAILABLE_RAM_MIB", 6144, 512, 262144),
        ("LLAMACPP_CONTEXT_SIZE", 131072, 512, 262144),
        ("LLAMACPP_CACHE_RAM_MIB", 8192, 0, 262144),
    ):
        value = data.get(key, str(default))
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} must be an integer") from exc
        if not minimum <= number <= maximum:
            raise ValueError(f"{key} must be between {minimum} and {maximum}")
        result[key] = number
    return result

if __name__ == "__main__":
    try:
        print(json.dumps(read_config(ROOT / "settings.yaml"), ensure_ascii=False))
    except (OSError, ValueError, TypeError) as exc:
        print(f"MAI llama.cpp model settings error: {exc}", file=sys.stderr)
        raise SystemExit(1)
