"""Resolve optional dev2 routed models from settings.yaml with GGUF preflight."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import yaml


def optional_models(settings: Path, root: Path) -> dict[str, str]:
    config = yaml.safe_load(settings.read_text(encoding="utf-8-sig"))
    if not isinstance(config, dict):
        raise ValueError("settings.yaml must be a mapping")
    result = {}
    for key in ("VISION_GGUF_PATH", "VISION_MMPROJ_PATH", "VERIFIER_GGUF_PATH"):
        raw = config.get(key)
        if raw is None or raw == "":
            result[key] = ""
            continue
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(f"{key} must be a nonempty path string")
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = root / path
        path = path.resolve()
        if path.suffix.lower() != ".gguf" or not path.is_file():
            raise ValueError(f"{key} must point to an existing GGUF: {path}")
        with path.open("rb") as stream:
            if stream.read(4) != b"GGUF":
                raise ValueError(f"{key} has no GGUF header: {path}")
        result[key] = str(path)
    if result["VISION_MMPROJ_PATH"] and not result["VISION_GGUF_PATH"]:
        raise ValueError("VISION_MMPROJ_PATH requires VISION_GGUF_PATH")
    return result


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: resolve_runtime_models.py settings.yaml project_root", file=sys.stderr)
        return 2
    try:
        result = optional_models(Path(sys.argv[1]), Path(sys.argv[2]))
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"Model routing preflight failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
