"""Read validated portable model asset paths from settings.yaml."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml


_PATH_KEYS = frozenset({"MODEL_GGUF_PATH", "MODEL_MMPROJ_PATH"})


def model_path(settings_path: Path, *, key: str = "MODEL_GGUF_PATH", optional: bool = False) -> str | None:
    if key not in _PATH_KEYS:
        raise ValueError(f"Unsupported model asset key: {key}")
    try:
        data = yaml.safe_load(settings_path.read_text(encoding="utf-8-sig"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"Unable to read settings.yaml: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("settings.yaml must contain a mapping")
    selected = data.get(key)
    if selected is None and optional:
        return None
    if not isinstance(selected, str) or not selected.strip():
        raise ValueError(f"{key} must be a nonempty string in settings.yaml")
    return selected.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve a configured portable GGUF asset")
    parser.add_argument("settings", nargs="?", type=Path, default=Path("settings.yaml"))
    parser.add_argument("--key", choices=sorted(_PATH_KEYS), default="MODEL_GGUF_PATH")
    parser.add_argument("--optional", action="store_true")
    args = parser.parse_args()
    try:
        selected = model_path(args.settings, key=args.key, optional=args.optional)
        if selected is not None:
            print(selected)
    except ValueError as exc:
        print(f"Portable GGUF setting error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
