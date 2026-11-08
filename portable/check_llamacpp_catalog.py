"""Validate configured GGUF catalog without starting or modifying a llama.cpp server."""
from __future__ import annotations

import argparse
from pathlib import Path

from portable.llamacpp_model_catalog import from_settings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    catalog = from_settings(args.root)
    default = next(model for model in catalog["models"]
                   if model["id"] == catalog["default"])
    print(f"GGUF models: {len(catalog['models'])}")
    print(f"Default GGUF: {default['label']}")


if __name__ == "__main__":
    main()
