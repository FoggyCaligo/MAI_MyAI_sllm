"""Download the upstream LongMemEval-S JSON as a verified, atomic artifact.

The Hub metadata HEAD step returned 400 for the user; request the documented
official resolve URL directly, rather than masking HTTP errors as cache failures.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import tempfile
from urllib.request import Request, urlopen

DATASET_REPO = "xiaowu0162/longmemeval-cleaned"
FILE = "longmemeval_s_cleaned.json"
SOURCE_URL = f"https://huggingface.co/datasets/{DATASET_REPO}/resolve/main/{FILE}"
# Published upstream Git LFS object hash and byte size. Fail if content differs.
EXPECTED_SHA256 = "d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442"
EXPECTED_BYTES = 277383467
USER_AGENT = "MAI/0.1 (https://github.com/FoggyCaligo/MAI_MyAI_sllm) LongMemEval"


def verify_file(path: Path) -> None:
    if path.stat().st_size != EXPECTED_BYTES:
        raise ValueError(
            f"LongMemEval-S size mismatch: expected {EXPECTED_BYTES}, got {path.stat().st_size}"
        )
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual != EXPECTED_SHA256:
        raise ValueError(
            f"LongMemEval-S sha256 mismatch: expected {EXPECTED_SHA256}, got {actual}"
        )


def download(destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        verify_file(destination)
        return destination

    # Download to same directory so os.replace is atomic on the same filesystem.
    fd, temporary = tempfile.mkstemp(prefix=f".{FILE}.", suffix=".partial", dir=destination.parent)
    temp_path = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as output:
            request = Request(SOURCE_URL, headers={"User-Agent": USER_AGENT})
            with urlopen(request, timeout=60) as response:
                if response.status != 200:
                    raise RuntimeError(f"LongMemEval download returned HTTP {response.status}")
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
        verify_file(temp_path)
        os.replace(temp_path, destination)
    finally:
        temp_path.unlink(missing_ok=True)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description="Download and verify upstream LongMemEval-S JSON")
    parser.add_argument(
        "--local-dir", type=Path,
        default=Path(__file__).resolve().parents[1] / "data",
    )
    args = parser.parse_args()
    path = download(args.local_dir / FILE)
    print(path.resolve())


if __name__ == "__main__":
    main()
