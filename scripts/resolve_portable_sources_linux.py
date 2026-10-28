"""Generate publisher SHA-256 pinned Ubuntu 24.04 x86-64 runtime sources.

Run with an existing Python installation on the preparation PC.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.resolve_portable_sources import resolve_one

SOURCES_LINUX = {
    "python": ("astral-sh", "python-build-standalone",
               r"cpython-3\.11\.[0-9]+\+[^/]+-x86_64-unknown-linux-gnu-install_only\.tar\.gz"),
    "uv": ("astral-sh", "uv", r"uv-x86_64-unknown-linux-gnu\.tar\.gz"),
    "ollama": ("ollama", "ollama", r"ollama-linux-amd64\.tar\.zst"),
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("portable-sources-linux.json"))
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"Refusing to overwrite pinned manifest: {args.output}")
    config: dict[str, object] = {"schema_version": 1, "target": "ubuntu-24.04-amd64"}
    for name, source in SOURCES_LINUX.items():
        config[name] = resolve_one(*source)
    args.output.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print(f"Linux portable sources saved: {args.output}")


if __name__ == "__main__":
    main()
