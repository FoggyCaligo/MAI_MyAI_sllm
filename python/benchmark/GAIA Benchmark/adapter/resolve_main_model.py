"""Resolve the benchmark main model using the exact Portable llama.cpp catalog contract."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys


def resolve_main_model(root: Path) -> str:
    sys.path.insert(0, str(root.resolve(strict=True)))
    from portable.llamacpp_model_catalog import from_settings

    catalog = from_settings(root)
    model = catalog["default"]
    if not isinstance(model, str) or not model.strip():
        raise ValueError("Portable model catalog has no valid default model")
    return model


def main() -> None:
    parser = argparse.ArgumentParser(description="Resolve GAIA benchmark model from Portable catalog")
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    print(resolve_main_model(args.root))


if __name__ == "__main__":
    main()
