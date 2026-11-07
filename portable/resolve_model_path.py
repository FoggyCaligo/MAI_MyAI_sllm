"""Print the selected portable GGUF path without Windows shell quoting pitfalls."""
from __future__ import annotations

import sys
from pathlib import Path

import yaml


def model_path(settings_path: Path) -> str:
    try:
        data = yaml.safe_load(settings_path.read_text(encoding="utf-8-sig"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"Unable to read settings.yaml: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("settings.yaml must contain a mapping")
    selected = data.get("MODEL_GGUF_PATH")
    if not isinstance(selected, str) or not selected.strip():
        raise ValueError("MODEL_GGUF_PATH must be a nonempty string in settings.yaml")
    return selected.strip()


def main() -> int:
    try:
        print(model_path(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("settings.yaml")))
    except ValueError as exc:
        print(f"Portable GGUF setting error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
