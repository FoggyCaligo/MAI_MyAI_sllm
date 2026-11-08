"""Resolve optional llama.cpp Portable model paths from settings.yaml.

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
    "LLAMACPP_VERIFIER_GGUF_PATH",
    "LLAMACPP_MAIN_MMPROJ_PATH",
    "LLAMACPP_MAIN_VISION",
    "LLAMACPP_VISION_GGUF_PATH",
    "LLAMACPP_VISION_MMPROJ_PATH",
)

def read_config(path: Path) -> dict[str, str | bool | None]:
    # YAML is the sole source of the model file paths, avoiding ambiguity
    # with stale .env model names (Ollama has its own model naming).
    data = load_settings(path, environ={})
    missing = [key for key in ("LLAMACPP_MAIN_GGUF_PATH",) if key not in data]
    if missing:
        raise ValueError("Missing llama.cpp GGUF setting(s): " + ", ".join(missing))
    raw_vision = data.get("LLAMACPP_MAIN_VISION", "false").lower()
    if raw_vision not in {"true", "false"}:
        raise ValueError("LLAMACPP_MAIN_VISION must be true or false")
    result: dict[str, str | bool | None] = {key: data.get(key) for key in KEYS}
    result["LLAMACPP_MAIN_VISION"] = raw_vision == "true"
    return result

if __name__ == "__main__":
    try:
        print(json.dumps(read_config(ROOT / "settings.yaml"), ensure_ascii=False))
    except (OSError, ValueError, TypeError) as exc:
        print(f"MAI llama.cpp model settings error: {exc}", file=sys.stderr)
        raise SystemExit(1)
