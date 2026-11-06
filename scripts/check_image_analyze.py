"""Reproduce MAI's image_analyze request against a local Ollama instance.

Run from the repository root:
    python scripts/check_image_analyze.py --path "C:\\path\\to\\image.png" --model gemma4:e4b

This uses ImageAnalyzer itself, not a parallel HTTP implementation. The output
includes input byte count and SHA-256 for correlating the exact image file
without emitting the image data.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from mai.tools.images import ImageAnalyzer


async def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", required=True, type=Path, help="Local image file")
    parser.add_argument("--model", required=True, help="Configured Ollama vision model")
    parser.add_argument("--host", default="http://127.0.0.1:11434", help="Ollama API address")
    parser.add_argument("--question", default="Describe the visible objects, colors, and text in this image.")
    args = parser.parse_args()

    image = args.path.expanduser().resolve(strict=True)
    data = image.read_bytes()
    analyzer = ImageAnalyzer(model=args.model, host=args.host)
    result = await analyzer.analyze(path=str(image), question=args.question)
    print(json.dumps({
        "image_path": str(image),
        "image_bytes": len(data),
        "image_sha256": hashlib.sha256(data).hexdigest(),
        "model": result["model"],
        "analysis": result["analysis"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(_main())
