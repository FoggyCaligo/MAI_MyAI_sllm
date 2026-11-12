"""Install verified official Google Gemma 4 12B QAT GGUF and projector."""
from __future__ import annotations
import argparse
import hashlib
import os
from pathlib import Path
import urllib.request

MODEL_DIRECTORY = Path("portable/portable-models/gguf/gemma4_12b_Q4")
BASE_URL = "https://huggingface.co/google/gemma-4-12B-it-qat-q4_0-gguf/resolve/main"
ARTIFACTS = (
    ("gemma-4-12b-it-qat-q4_0.gguf", "93567e57a8fe10b23569b9d9ec38cd005deedf71e29477c421a4b83f418a538b"),
    ("mmproj-gemma-4-12b-it-qat-q4_0.gguf", "cb018338a7538a9814d994bfe54644c71eb7ed54e31eae2f721e45fd3c260da7"),
)
CHUNK_SIZE = 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(CHUNK_SIZE), b""):
            digest.update(block)
    return digest.hexdigest()


def install(root: Path, *, fetch=None) -> None:
    dest = root.resolve() / MODEL_DIRECTORY
    dest.mkdir(parents=True, exist_ok=True)
    fetch = fetch or urllib.request.urlopen
    for filename, expected in ARTIFACTS:
        target = dest / filename
        if target.exists():
            if not target.is_file() or sha256_file(target) != expected:
                raise RuntimeError(f"Existing Gemma artifact failed SHA-256 (preserved): {target}")
            print(f"Verified existing Gemma artifact: {target}", flush=True)
            continue
        partial = target.with_name(filename + ".incomplete")
        if partial.exists():
            raise RuntimeError(f"Preexisting incomplete Gemma download: {partial}")
        digest = hashlib.sha256()
        request = urllib.request.Request(
            f"{BASE_URL}/{filename}", headers={"User-Agent": "MAI-setup/1.0"}
        )
        try:
            with fetch(request, timeout=120) as response, partial.open("xb") as output:
                while True:
                    data = response.read(CHUNK_SIZE)
                    if not data:
                        break
                    digest.update(data)
                    output.write(data)
            if digest.hexdigest() != expected:
                raise RuntimeError(f"Gemma SHA-256 mismatch for {filename}: {digest.hexdigest()} != {expected}")
            if target.exists():
                raise FileExistsError(f"Gemma target appeared during download: {target}")
            os.rename(partial, target)
            print(f"Installed verified Gemma artifact: {target}", flush=True)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    install(parser.parse_args().root)


if __name__ == "__main__":
    main()
